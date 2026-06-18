import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './Assistant.module.css'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
}

interface Conversation {
  id: number
  title: string
  msg_count: number
  updated_at: string
}

export default function Assistant() {
  const { showToast } = useToast()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [statusOk, setStatusOk] = useState<boolean | null>(null)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Check LLM status — uses separate axios call that won't trigger 401 redirect
  useEffect(() => {
    api.get('/api/assistant/status')
      .then(r => setStatusOk(r.data?.ok === true))
      .catch(() => setStatusOk(false))
  }, [])

  // Load conversation list
  const loadConversations = async () => {
    try {
      const r = await api.get('/api/assistant/conversations')
      // API returns { conversations: [...] }
      const list = r.data?.conversations ?? r.data ?? []
      setConversations(Array.isArray(list) ? list : [])
    } catch { /* silent */ }
  }

  useEffect(() => { loadConversations() }, [])

  // Load suggestions
  useEffect(() => {
    api.get('/api/assistant/suggestions')
      .then(r => {
        const s = r.data?.suggestions ?? r.data ?? []
        setSuggestions(Array.isArray(s) ? s : [])
      })
      .catch(() => {})
  }, [])

  // Scroll to bottom on new message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const openConversation = async (convId: number) => {
    setActiveConvId(convId)
    try {
      const r = await api.get(`/api/assistant/conversations/${convId}`)
      // API returns { conversation: {...}, messages: [...] }
      const msgs = r.data?.messages ?? []
      setMessages(Array.isArray(msgs) ? msgs : [])
    } catch { showToast('Erreur chargement conversation', 'error') }
  }

  const newConversation = () => {
    setActiveConvId(null)
    setMessages([])
    setInput('')
    inputRef.current?.focus()
  }

  const deleteConversation = async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api.delete(`/api/assistant/conversations/${convId}`)
      if (activeConvId === convId) newConversation()
      loadConversations()
    } catch { showToast('Erreur suppression', 'error') }
  }

  const sendMessage = async (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg || loading) return
    setInput('')

    const userMsg: Message = { role: 'user', content: msg, timestamp: new Date().toISOString() }
    setMessages(prev => [...prev, userMsg])
    setLoading(true)

    try {
      const r = await api.post('/api/assistant/chat', {
        message: msg,
        conversation_id: activeConvId,
      })
      const reply: Message = {
        role: 'assistant',
        content: r.data.reply ?? '…',
        timestamp: r.data.timestamp,
      }
      setMessages(prev => [...prev, reply])
      if (r.data.is_new_conv || !activeConvId) {
        setActiveConvId(r.data.conversation_id)
        loadConversations()
      }
    } catch (err: any) {
      showToast(err?.response?.data?.error ?? 'Erreur LLM', 'error')
      setMessages(prev => prev.slice(0, -1))
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  // Minimal markdown: **bold**, - lists, newlines
  const renderContent = (text: string) => {
    const lines = text.split('\n')
    const parts: React.ReactNode[] = []
    let listItems: React.ReactNode[] = []

    const flushList = () => {
      if (listItems.length > 0) {
        parts.push(<ul key={`ul-${parts.length}`} className={styles.msgList}>{listItems}</ul>)
        listItems = []
      }
    }

    lines.forEach((line, i) => {
      const isList = line.match(/^[-•*]\s+/)
      const boldLine = line.replace(/\*\*(.*?)\*\*/g, (_, m) => `<strong>${m}</strong>`)
      const content = <span key={i} dangerouslySetInnerHTML={{ __html: boldLine.replace(isList ? /^[-•*]\s+/ : '', '') }} />

      if (isList) {
        listItems.push(<li key={i}>{content}</li>)
      } else {
        flushList()
        if (line.trim()) {
          parts.push(<p key={i} className={styles.msgPara}>{content}</p>)
        }
      }
    })
    flushList()
    return parts
  }

  return (
          <div className={styles.shell}>
        {/* Sidebar conversations */}
        <aside className={styles.convPanel}>
          <div className={styles.convHeader}>
            <span style={{ fontWeight: 800, fontSize: 14 }}>🤖 Conversations</span>
            <button className="btn bp bxs" style={{ fontSize: 11, padding: '5px 10px' }} onClick={newConversation}>+ Nouveau</button>
          </div>

          {/* Status badge */}
          <div className={styles.statusBadge} style={{ color: statusOk === true ? 'var(--green)' : statusOk === false ? 'var(--orange)' : 'var(--mut)' }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: statusOk === true ? 'var(--green)' : statusOk === false ? 'var(--orange)' : 'var(--mut)',
              display: 'inline-block', marginRight: 5
            }} />
            {statusOk === true ? 'LLM connecté' : statusOk === false ? 'LLM non configuré' : 'Vérification...'}
          </div>

          <div className={styles.convList}>
            {conversations.length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--mut)', padding: '12px 8px', textAlign: 'center' }}>
                Aucune conversation
              </div>
            )}
            {conversations.map(c => (
              <div
                key={c.id}
                className={`${styles.convItem} ${activeConvId === c.id ? styles.convActive : ''}`}
                onClick={() => openConversation(c.id)}
              >
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div className={styles.convTitle}>{c.title}</div>
                  <div className={styles.convMeta}>{c.msg_count} msg · {new Date(c.updated_at).toLocaleDateString('fr-FR')}</div>
                </div>
                <button
                  className={styles.delBtn}
                  onClick={e => deleteConversation(c.id, e)}
                  title="Supprimer"
                >×</button>
              </div>
            ))}
          </div>
        </aside>

        {/* Chat area */}
        <div className={styles.chatArea}>
          <div className={styles.messages}>
            {messages.length === 0 && (
              <div className={styles.welcome}>
                <div style={{ fontSize: 44, marginBottom: 12 }}>🤖</div>
                <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 6 }}>Assistant IA — Flux Monitor</div>
                <div style={{ fontSize: 13, color: 'var(--mut)', maxWidth: 440, textAlign: 'center', marginBottom: 20 }}>
                  Consultez vos flux, alertes, statistiques — et agissez directement depuis le chat.
                </div>
                {suggestions.length > 0 && (
                  <div className={styles.suggestions}>
                    {suggestions.slice(0, 4).map((s, i) => (
                      <button key={i} className={styles.suggestBtn} onClick={() => sendMessage(s)}>{s}</button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`${styles.msgRow} ${m.role === 'user' ? styles.msgUser : styles.msgBot}`}>
                <div className={styles.msgAvatar}>{m.role === 'user' ? '👤' : '🤖'}</div>
                <div className={styles.msgBubble}>
                  <div className={styles.msgContent}>{renderContent(m.content)}</div>
                  {m.timestamp && (
                    <div className={styles.msgTime}>
                      {new Date(m.timestamp).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className={`${styles.msgRow} ${styles.msgBot}`}>
                <div className={styles.msgAvatar}>🤖</div>
                <div className={styles.msgBubble}>
                  <div className={styles.msgContent}>
                    <div className={styles.typing}><span /><span /><span /></div>
                  </div>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className={styles.inputArea}>
            <textarea
              ref={inputRef}
              className={styles.inputBox}
              placeholder="Posez une question… (Entrée pour envoyer, Maj+Entrée pour saut de ligne)"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              rows={2}
              disabled={loading}
            />
            <button
              className="btn bp"
              style={{ width: 42, height: 42, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, flexShrink: 0, borderRadius: 10 }}
              onClick={() => sendMessage()}
              disabled={!input.trim() || loading}
            >
              {loading ? <span className="spin" /> : '➤'}
            </button>
          </div>
        </div>
      </div>
  )
}