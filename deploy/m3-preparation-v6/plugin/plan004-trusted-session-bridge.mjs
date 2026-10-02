import net from "node:net"

const CARRIER_KEY = "__aota_trusted_invocation"
const TOOL_NAME = "aota_aota_invoke"
const SOCKET_ENV = "AOTA_BRIDGE_SOCKET"
const MAX_RESPONSE_BYTES = 16 * 1024

function request(socketPath, payload) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath)
    let data = ""
    let settled = false
    const fail = (error) => {
      if (settled) return
      settled = true
      socket.destroy()
      reject(error)
    }
    socket.setTimeout(2000, () => fail(new Error("trusted bridge IPC timeout")))
    socket.on("error", fail)
    socket.on("data", (chunk) => {
      data += chunk.toString("utf8")
      if (Buffer.byteLength(data, "utf8") > MAX_RESPONSE_BYTES) {
        fail(new Error("trusted bridge response exceeds its bound"))
        return
      }
      const end = data.indexOf("\n")
      if (end < 0) return
      const line = data.slice(0, end)
      settled = true
      socket.end()
      try {
        const response = JSON.parse(line)
        if (response?.ok !== true) {
          const error = response?.error
          reject(new Error(typeof error?.message === "string" ? error.message : "trusted bridge rejected request"))
          return
        }
        resolve(response)
      } catch {
        reject(new Error("trusted bridge response is malformed"))
      }
    })
    socket.on("connect", () => socket.write(`${JSON.stringify(payload)}\n`))
  })
}

export default async function Plan004TrustedSessionBridge() {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== TOOL_NAME) return
      const socketPath = process.env[SOCKET_ENV]
      if (!socketPath) throw new Error("trusted bridge socket is not configured")
      const args = output.args
      if (!args || typeof args !== "object" || typeof args.operation !== "string") {
        throw new Error("trusted AOTA invocation arguments are malformed")
      }
      if (!args.arguments || typeof args.arguments !== "object" || Array.isArray(args.arguments)) {
        throw new Error("trusted AOTA logical arguments are malformed")
      }
      const logicalArguments = { ...args.arguments }
      delete logicalArguments[CARRIER_KEY]
      const response = await request(socketPath, {
        op: "issue",
        session_id: input.sessionID,
        call_id: input.callID,
        operation: args.operation,
        arguments: logicalArguments,
      })
      if (typeof response.token !== "string" || !response.token) {
        throw new Error("trusted bridge returned no invocation token")
      }
      args.arguments = {
        ...logicalArguments,
        [CARRIER_KEY]: { schema: "aota-bridge-v1", token: response.token },
      }
    },
  }
}
