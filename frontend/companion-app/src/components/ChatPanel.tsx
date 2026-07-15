import { useState, useRef, useEffect } from "react";
import { Send, ShoppingBag, Bot, User, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useChatStore } from "@/stores/chatStore";
import { cn } from "@/lib/cn";

interface ChatPanelProps {
  sessionId: string;
  onSendMessage: (text: string, mode: "chat_only" | "voice_and_chat") => void;
  onApproveDraft: (messageId: string, editedText?: string) => void;
  onRejectDraft: (messageId: string) => void;
  onLockDraft: (messageId: string) => void;
}

export function ChatPanel({
  sessionId,
  onSendMessage,
  onApproveDraft,
  onRejectDraft,
  onLockDraft,
}: ChatPanelProps) {
  const messages = useChatStore((s) => s.messages);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<"chat_only" | "voice_and_chat">("chat_only");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  const handleSend = () => {
    if (!input.trim()) return;
    onSendMessage(input.trim(), mode);
    setInput("");
  };

  return (
    <div
      className="flex h-full flex-col rounded-lg border border-border bg-surface"
      data-testid="chat-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-text">Live Chat</h3>
        <Badge variant="secondary">{messages.length} msgs</Badge>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="py-8 text-center text-sm text-text-muted">
            Chat messages will appear here during the live stream.
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "rounded-lg p-3 text-sm",
              msg.is_purchase
                ? "border border-success/30 bg-success/10"
                : "bg-card"
            )}
            data-testid={`chat-message-${msg.id}`}
          >
            {/* Viewer message */}
            <div className="flex items-center gap-2">
              {msg.is_purchase ? (
                <ShoppingBag className="h-3 w-3 text-success" />
              ) : (
                <User className="h-3 w-3 text-text-muted" />
              )}
              <span className="font-medium text-accent">{msg.viewer_username}</span>
              {msg.is_purchase && (
                <Badge variant="success" className="text-[10px]">
                  PURCHASE
                </Badge>
              )}
            </div>
            <p className="mt-1 text-text-dim">{msg.message_text}</p>

            {/* AI Draft */}
            {msg.ai_draft && msg.ai_draft_status === "pending_approval" && (
              <div className="mt-2 rounded border border-accent/20 bg-accent/5 p-2">
                <div className="mb-1 flex items-center gap-1 text-xs text-accent">
                  <Bot className="h-3 w-3" />
                  AI Draft
                  {msg.locked_by && (
                    <span className="ml-2 flex items-center gap-1 text-warning">
                      <Lock className="h-3 w-3" />
                      Editing...
                    </span>
                  )}
                </div>
                <p className="text-xs text-text-dim">{msg.ai_draft}</p>
                {!msg.locked_by && (
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      variant="success"
                      onClick={() => onApproveDraft(msg.id)}
                      data-testid={`approve-draft-${msg.id}`}
                      className="h-6 text-xs"
                    >
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onLockDraft(msg.id)}
                      data-testid={`edit-draft-${msg.id}`}
                      className="h-6 text-xs"
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onRejectDraft(msg.id)}
                      data-testid={`reject-draft-${msg.id}`}
                      className="h-6 text-xs text-danger"
                    >
                      Reject
                    </Button>
                  </div>
                )}
              </div>
            )}

            {msg.ai_draft_status === "auto_sent" && (
              <div className="mt-1 flex items-center gap-1 text-xs text-text-muted">
                <Bot className="h-3 w-3" />
                Auto-sent by AI
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="border-t border-border p-3">
        <div className="mb-2 flex gap-2">
          <button
            onClick={() => setMode("chat_only")}
            className={cn(
              "rounded px-2 py-1 text-xs transition-colors",
              mode === "chat_only"
                ? "bg-accent/20 text-accent"
                : "text-text-muted hover:text-text-dim"
            )}
            data-testid="chat-mode-text"
          >
            Text Only
          </button>
          <button
            onClick={() => setMode("voice_and_chat")}
            className={cn(
              "rounded px-2 py-1 text-xs transition-colors",
              mode === "voice_and_chat"
                ? "bg-accent/20 text-accent"
                : "text-text-muted hover:text-text-dim"
            )}
            data-testid="chat-mode-voice"
          >
            Voice + Chat
          </button>
        </div>
        <div className="flex gap-2">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Type a message..."
            data-testid="chat-input"
            className="flex-1"
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={!input.trim()}
            data-testid="chat-send"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
