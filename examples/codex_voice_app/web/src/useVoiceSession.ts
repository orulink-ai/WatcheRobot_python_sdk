import { useCallback, useEffect, useRef, useState } from "react"
import { initialSession, reduceEvent } from "./session"

export function useVoiceSession() {
  const [session, setSession] = useState(initialSession)
  const [backendConnected, setBackendConnected] = useState(false)
  const socket = useRef<WebSocket | null>(null)

  useEffect(() => {
    let disposed = false
    let retry: ReturnType<typeof setTimeout> | undefined
    const connect = () => {
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/session`)
      socket.current = ws
      ws.onopen = () => { if (!disposed) setBackendConnected(true) }
      ws.onmessage = (message) => {
        if (disposed) return
        try { setSession((state) => reduceEvent(state, JSON.parse(message.data))) }
        catch { setSession((state) => ({ ...state, error: "收到无法解析的会话数据" })) }
      }
      ws.onclose = () => {
        if (disposed) return
        setBackendConnected(false)
        setSession((state) => reduceEvent(state, { type: "closed" }))
        retry = setTimeout(connect, 2000)
      }
      ws.onerror = () => ws.close()
    }
    connect()
    return () => { disposed = true; clearTimeout(retry); socket.current?.close() }
  }, [])

  const command = useCallback((payload: Record<string, unknown>) => {
    if (socket.current?.readyState !== WebSocket.OPEN) throw new Error("Application 尚未连接")
    socket.current.send(JSON.stringify(payload))
  }, [])
  return { session, backendConnected, command }
}
