/**
 * AF #58 M2 — bounded AF interactive ingress seam for the OpenChamber :3002
 * AF Reference instance.
 *
 * Upstream-neutral and DISABLED BY DEFAULT: the module returns null unless
 * `AF_INTERACTIVE_INGRESS_ENABLED=true` is set for this exact instance (the
 * 3000/3001 instances do not set it and keep their behavior unchanged).
 *
 * What it does, mechanically:
 *
 *   POST /api/session
 *     -> AF `reserve` (mint a unique per-chat instance namespace under the
 *        operator-owned AF interactive workspace root; no authority)
 *     -> rewrite the session-create directory to that namespace
 *     -> mark the session row metadata with the preparation id
 *
 *   POST /api/session/:id/prompt_async|prompt|command
 *     -> require the exact AF host profile
 *     -> AF `bind` (verify the exact session id / directory / metadata
 *        against the pinned host, read the LIVE Plan through the accepted AF
 *        Plan-authority path, resolve project/source/root through the
 *        accepted AF trusted binding, materialize the EXISTING digest-bound
 *        task-main binding envelope + pointer)
 *     -> ONLY THEN rewrite the directory to the bound instance namespace and
 *        let the original operator message through to OpenCode
 *     -> on any failure: bounded JSON error, message is NOT dispatched
 *
 * Other /api/session/<id>/* requests get their directory rewritten to the
 * session's AF instance namespace so the UI keeps talking to the exact
 * directory instance whose MCP child owns the trusted binding.
 *
 * This module contains no authority logic: every trust decision is made by
 * the AF composition behind the one-shot CLI it spawns.
 */

import { spawn } from 'node:child_process';
import express from 'express';

const INTERACTIVE_SCHEMA = 'af58-m2-interactive-v1';
const PREPARATION_METADATA_KEY = 'af_interactive_preparation';
const SCHEMA_METADATA_KEY = 'af_interactive_schema';

const RESERVE_TIMEOUT_MS = 30_000;
const DEFAULT_BIND_TIMEOUT_MS = 180_000;
const MAX_STDOUT_BYTES = 1_048_576;
const MAX_STDERR_BYTES = 65_536;
const MAX_MESSAGE_BYTES = 64 * 1024;

const trimString = (value) => (typeof value === 'string' ? value.trim() : '');

export const isAfInteractiveIngressEnabled = (env = process.env) =>
  String(env?.AF_INTERACTIVE_INGRESS_ENABLED ?? '').trim().toLowerCase() === 'true';

const runCli = (config, args, { stdin = '', timeoutMs = DEFAULT_BIND_TIMEOUT_MS } = {}) =>
  new Promise((resolve) => {
    const operatorBin = config.ghConfigDir ? config.ghConfigDir.replace(/\/\.config\/gh$/, '/.local/bin') : '';
    const env = {
      ...process.env,
      PATH: [operatorBin, process.env.PATH].filter(Boolean).join(':'),
      PYTHONPATH: [config.repoRoot, process.env.PYTHONPATH].filter(Boolean).join(':'),
      AOTA_FORGE_REPO_ROOT: config.repoRoot,
      AF_INTERACTIVE_WORKSPACE_ROOT: config.workspaceRoot,
      AF_INTERACTIVE_RUNTIME_CONFIG: config.runtimeConfig,
      AF_INTERACTIVE_REGISTRY: config.registry,
    };
    if (config.ghConfigDir) env.GH_CONFIG_DIR = config.ghConfigDir;
    let settled = false;
    let stdout = '';
    let stderr = '';
    let child;
    const finish = (payload) => {
      if (settled) return;
      settled = true;
      resolve(payload);
    };
    try {
      child = spawn(
        config.python,
        ['-m', 'aota_forge.interactive_ingress.cli', ...args],
        { env, stdio: ['pipe', 'pipe', 'pipe'] },
      );
    } catch (error) {
      finish({ ok: false, code: 'AF_CLI_SPAWN_FAILED', message: String(error?.message || error).slice(0, 300) });
      return;
    }
    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL');
      } catch (error) {
        void error;
      }
      finish({ ok: false, code: 'AF_CLI_TIMEOUT', message: `AF interactive ingress CLI timed out after ${timeoutMs}ms` });
    }, timeoutMs);
    timer.unref?.();
    child.stdout.on('data', (chunk) => {
      if (stdout.length < MAX_STDOUT_BYTES) stdout += chunk.toString('utf8');
    });
    child.stderr.on('data', (chunk) => {
      if (stderr.length < MAX_STDERR_BYTES) stderr += chunk.toString('utf8');
    });
    child.on('error', (error) => {
      clearTimeout(timer);
      finish({ ok: false, code: 'AF_CLI_SPAWN_FAILED', message: String(error?.message || error).slice(0, 300) });
    });
    child.on('close', () => {
      clearTimeout(timer);
      const line = stdout
        .split('\n')
        .map((entry) => entry.trim())
        .filter(Boolean)
        .pop();
      if (!line) {
        finish({
          ok: false,
          code: 'AF_CLI_NO_OUTPUT',
          message: (stderr.trim() || 'AF interactive ingress CLI produced no output').slice(0, 300),
        });
        return;
      }
      try {
        finish(JSON.parse(line));
      } catch {
        finish({ ok: false, code: 'AF_CLI_MALFORMED_OUTPUT', message: line.slice(0, 300) });
      }
    });
    try {
      if (stdin) child.stdin.write(stdin);
      child.stdin.end();
    } catch (error) {
      finish({ ok: false, code: 'AF_CLI_STDIN_FAILED', message: String(error?.message || error).slice(0, 300) });
    }
  });

const readJsonBody = (req) =>
  req.body && typeof req.body === 'object' && !Array.isArray(req.body) ? req.body : {};

const rewriteDirectoryQuery = (req, directory) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    url.searchParams.set('directory', directory);
    req.url = `${url.pathname}${url.search}`;
  } catch {
    return;
  }
  try {
    req.headers['x-opencode-directory'] = encodeURIComponent(directory);
    req.headers['x-opencode-directory-encoding'] = 'uri';
  } catch {
    return;
  }
};

const rewriteRequestBodyDirectory = (req, directory) => {
  const body = req.body;
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    body.directory = directory;
  }
};

const extractPromptText = (body) => {
  const parts = Array.isArray(body?.parts) ? body.parts : [];
  const text = parts
    .filter((part) => part && typeof part === 'object' && part.type === 'text' && typeof part.text === 'string')
    .map((part) => part.text)
    .join('\n');
  if (text) return text.slice(0, MAX_MESSAGE_BYTES);
  const command = trimString(body?.command);
  if (command) {
    const args = Array.isArray(body?.arguments) ? body.arguments.filter((entry) => typeof entry === 'string') : [];
    return [command, ...args].join(' ').slice(0, MAX_MESSAGE_BYTES);
  }
  return '';
};

export class AfInteractiveIngress {
  constructor(config, deps = {}) {
    this.config = config;
    this.deps = deps;
    this.sessionDirectories = new Map();
    this.missing = [];
    if (!config.profile) this.missing.push('AF_INTERACTIVE_PROFILE');
    if (!config.repoRoot) this.missing.push('AF_INTERACTIVE_REPO_ROOT');
    if (!config.workspaceRoot) this.missing.push('AF_INTERACTIVE_WORKSPACE_ROOT');
    if (!config.runtimeConfig) this.missing.push('AF_INTERACTIVE_RUNTIME_CONFIG');
    if (!config.registry) this.missing.push('AF_INTERACTIVE_REGISTRY');
  }

  register(app) {
    if (!express || typeof express.json !== 'function') {
      throw new Error('AF interactive ingress requires the express dependency');
    }
    const jsonParser = express.json({ limit: '2mb' });
    app.post('/api/session', jsonParser, (req, res, next) => this.handleCreate(req, res, next));
    app.use('/api/session', jsonParser, (req, res, next) => this.handleSessionScoped(req, res, next));
    app.post('/api/session/:sessionId/prompt_async', (req, res, next) => this.handlePrompt(req, res, next));
    app.post('/api/session/:sessionId/prompt', (req, res, next) => this.handlePrompt(req, res, next));
    app.post('/api/session/:sessionId/command', (req, res, next) => this.handlePrompt(req, res, next));
  }

  sendError(res, status, code, message) {
    const payload = { error: message, code };
    if (res.headersSent) {
      try {
        res.end();
      } catch (error) {
        void error;
      }
      return;
    }
    res.status(status).json(payload);
  }

  requireConfigured(res) {
    if (this.missing.length === 0) return false;
    this.sendError(
      res,
      503,
      'AF_INGRESS_MISCONFIGURED',
      `AF interactive ingress is enabled but missing configuration: ${this.missing.join(', ')}`,
    );
    return true;
  }

  async handleCreate(req, res, next) {
    try {
      if (this.requireConfigured(res)) return;
      const body = readJsonBody(req);
      if (body.parentID) return next();
      const reserved = await runCli(this.config, ['reserve'], { timeoutMs: RESERVE_TIMEOUT_MS });
      if (!reserved || reserved.ok !== true || !trimString(reserved.instance_dir) || !trimString(reserved.preparation_id)) {
        return this.sendError(
          res,
          503,
          reserved?.code || 'AF_PREPARE_FAILED',
          reserved?.message || 'AF interactive preparation failed',
        );
      }
      const directory = String(reserved.instance_dir);
      rewriteDirectoryQuery(req, directory);
      const metadata = body.metadata && typeof body.metadata === 'object' && !Array.isArray(body.metadata)
        ? { ...body.metadata }
        : {};
      metadata[PREPARATION_METADATA_KEY] = String(reserved.preparation_id);
      metadata[SCHEMA_METADATA_KEY] = INTERACTIVE_SCHEMA;
      req.body = { ...body, metadata };
      rewriteRequestBodyDirectory(req, directory);
      return next();
    } catch (error) {
      console.error('[AFInteractiveIngress] create failed:', error?.message || error);
      return this.sendError(res, 503, 'AF_PREPARE_FAILED', 'AF interactive preparation failed');
    }
  }

  async resolveSessionDirectory(sessionID) {
    if (this.sessionDirectories.has(sessionID)) return this.sessionDirectories.get(sessionID);
    try {
      const { buildOpenCodeUrl, getOpenCodeAuthHeaders } = this.deps;
      const url = buildOpenCodeUrl(`/session/${encodeURIComponent(sessionID)}`, '');
      const response = await fetch(url, {
        headers: { accept: 'application/json', ...getOpenCodeAuthHeaders() },
      });
      if (!response.ok) return null;
      const row = await response.json().catch(() => null);
      const directory = trimString(row?.directory);
      const metadata = row?.metadata && typeof row.metadata === 'object' ? row.metadata : {};
      if (!directory) return null;
      if (!trimString(metadata[PREPARATION_METADATA_KEY])) return null;
      this.sessionDirectories.set(sessionID, directory);
      return directory;
    } catch {
      return null;
    }
  }

  async handleSessionScoped(req, res, next) {
    try {
      const match = /^\/([^/?#]+)/.exec(req.path || '');
      const sessionID = match ? decodeURIComponent(match[1]) : '';
      if (!sessionID.startsWith('ses_')) return next();
      const directory = await this.resolveSessionDirectory(sessionID);
      if (directory) {
        rewriteDirectoryQuery(req, directory);
        rewriteRequestBodyDirectory(req, directory);
      }
      return next();
    } catch (error) {
      console.error('[AFInteractiveIngress] session-scoped rewrite failed:', error?.message || error);
      return next();
    }
  }

  async handlePrompt(req, res, next) {
    try {
      if (this.requireConfigured(res)) return;
      const body = readJsonBody(req);
      const agent = typeof body.agent === 'string' ? body.agent.trim() : '';
      if (agent !== this.config.profile) {
        return this.sendError(
          res,
          409,
          'AF_PROFILE_REQUIRED',
          `This OpenChamber instance is the AF interactive surface. Select the '${this.config.profile}' profile (or create a New Chat with it) before sending.`,
        );
      }
      const sessionID = String(req.params?.sessionId || '');
      if (!sessionID.startsWith('ses_')) {
        return this.sendError(res, 409, 'AF_UNKNOWN_SESSION', 'AF interactive ingress requires an exact session id');
      }
      const message = extractPromptText(body);
      if (!message.trim()) {
        return this.sendError(res, 409, 'AF_MESSAGE_EMPTY', 'AF interactive ingress requires a non-empty operator message');
      }
      const bound = await runCli(
        this.config,
        ['bind', '--session-id', sessionID, '--profile', agent, '--message-file', '-'],
        { stdin: message, timeoutMs: this.config.timeoutMs },
      );
      if (!bound || bound.ok !== true) {
        return this.sendError(
          res,
          409,
          bound?.code || 'AF_BIND_FAILED',
          bound?.message || 'AF trusted interactive bind failed; the message was not dispatched',
        );
      }
      const directory = String(bound.instance_dir || '');
      if (!directory) {
        return this.sendError(res, 409, 'AF_BIND_FAILED', 'AF trusted bind returned no instance directory');
      }
      this.sessionDirectories.set(sessionID, directory);
      rewriteDirectoryQuery(req, directory);
      rewriteRequestBodyDirectory(req, directory);
      return next();
    } catch (error) {
      console.error('[AFInteractiveIngress] bind failed:', error?.message || error);
      return this.sendError(res, 503, 'AF_BIND_FAILED', 'AF trusted interactive bind failed');
    }
  }
}

export const createAfInteractiveIngress = ({ env = process.env, ...deps } = {}) => {
  if (!isAfInteractiveIngressEnabled(env)) return null;
  const home = env.HOME || process.env.HOME || '';
  const config = {
    profile: trimString(env.AF_INTERACTIVE_PROFILE),
    python: trimString(env.AF_INTERACTIVE_PYTHON) || '/usr/bin/python3',
    repoRoot: trimString(env.AF_INTERACTIVE_REPO_ROOT),
    workspaceRoot: trimString(env.AF_INTERACTIVE_WORKSPACE_ROOT),
    runtimeConfig: trimString(env.AF_INTERACTIVE_RUNTIME_CONFIG),
    registry: trimString(env.AF_INTERACTIVE_REGISTRY),
    timeoutMs: Number.parseInt(env.AF_INTERACTIVE_TIMEOUT_MS || '', 10) || DEFAULT_BIND_TIMEOUT_MS,
    ghConfigDir: trimString(env.GH_CONFIG_DIR) || (home ? `${home}/.config/gh` : ''),
  };
  return new AfInteractiveIngress(config, deps);
};
