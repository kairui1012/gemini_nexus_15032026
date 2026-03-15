function ChatBubble({ role, content }) {
  const isUser = role === "user";

  return (
    <div className={`bubble-row ${isUser ? "user" : "ai"}`}>
      <div className="bubble-meta">{isUser ? "You" : "Vertex AI"}</div>
      <div className={`bubble ${isUser ? "bubble-user" : "bubble-ai"}`}>{content}</div>
    </div>
  );
}

export default ChatBubble;
