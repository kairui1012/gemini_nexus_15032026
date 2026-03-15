import { useState } from "react";
import ChatBubble from "./ChatBubble";
import { sendMessage, uploadCsv } from "./api";

function parseQuantSections(rawReply) {
  const text = String(rawReply || "").trim();
  if (!text || !/Data Insights/i.test(text)) {
    return {
      mainReply: text,
      insights: [],
      metrics: ""
    };
  }

  const chunks = text.split(/\nData Insights\s*\n/i);
  if (chunks.length < 2) {
    return {
      mainReply: text,
      insights: [],
      metrics: ""
    };
  }

  const mainReply = chunks[0].trim();
  const insightRaw = chunks.slice(1).join("\n");
  const lines = insightRaw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const insights = [];
  let metrics = "";

  lines.forEach((line) => {
    const normalized = line.replace(/^[-*]\s*/, "").trim();
    if (!normalized) return;

    if (/^Computed metrics:/i.test(normalized)) {
      const payload = normalized.replace(/^Computed metrics:\s*/i, "").trim();
      try {
        const parsed = JSON.parse(payload);
        metrics = JSON.stringify(parsed, null, 2);
      } catch {
        metrics = payload;
      }
      return;
    }

    insights.push(normalized);
  });

  return {
    mainReply: mainReply || text,
    insights,
    metrics
  };
}

function App() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([
    { role: "ai", content: "Hi" }
  ]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [csvFilePath, setCsvFilePath] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("idle");
  const [insightsPanel, setInsightsPanel] = useState([]);
  const [metricsPanel, setMetricsPanel] = useState("");

  const selectedName = selectedFile?.name || "No file selected";

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0] || null;
    setSelectedFile(file);
    setCsvFilePath("");
    setError("");

    if (!file || uploading || loading) {
      setUploadStatus("idle");
      return;
    }

    setUploading(true);
    setUploadStatus("uploading");
    setError("");
    try {
      const data = await uploadCsv(file);
      setCsvFilePath(data.csv_file_path || "");
      setUploadStatus("ready");
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: `CSV uploaded: ${data.file_name || file.name}\nPath: ${data.csv_file_path || "(unknown)"}`
        }
      ]);
    } catch (err) {
      setUploadStatus("failed");
      setError(err.message || "Failed to upload CSV.");
    } finally {
      setUploading(false);
    }
  };

  const handleSend = async (event) => {
    event.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const nextMessages = [...messages, { role: "user", content: text }];
    setMessages(nextMessages);
    setInput("");
    setError("");
    setLoading(true);

    try {
      const data = await sendMessage(text, nextMessages, csvFilePath || null);
      const { mainReply, insights, metrics } = parseQuantSections(data.reply);

      if (insights.length > 0) {
        setInsightsPanel(insights);
      }
      if (metrics) {
        setMetricsPanel(metrics);
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: mainReply || "Analysis complete."
        }
      ]);
    } catch (err) {
      setError(err.message || "Failed to send. Please try again later.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page">
      <section className="chat-card">
        <div className="chat-workspace">
          <aside className="insights-panel">
            <article className="insight-card">
              <h3>Data Insights</h3>
              {insightsPanel.length > 0 ? (
                <ul>
                  {insightsPanel.map((item, idx) => (
                    <li key={`${item}-${idx}`}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p>No insights yet.</p>
              )}
            </article>

            <article className="insight-card">
              <h3>Computed Metrics</h3>
              {metricsPanel ? <pre>{metricsPanel}</pre> : <p>No metrics yet.</p>}
            </article>
          </aside>

          <section className="chat-main">
            <header className="chat-header">
              <h1>Quantitative Forge Chat</h1>
              <p>React + FastAPI + Vertex AI</p>
            </header>

            <div className="chat-list">
              {messages.map((item, index) => (
                <ChatBubble key={`${item.role}-${index}`} role={item.role} content={item.content} />
              ))}
              {loading && <ChatBubble role="ai" content="Thinking..." />}
            </div>

            {error && <div className="error-box">{error}</div>}

            <div className="file-panel">
              <div className="file-head">
                <label className="file-label" htmlFor="csv-file-input">CSV File</label>
                <span className={`file-badge ${uploadStatus}`}>
                  {uploadStatus === "uploading" && "Uploading"}
                  {uploadStatus === "ready" && "Ready"}
                  {uploadStatus === "failed" && "Upload Failed"}
                  {uploadStatus === "idle" && "Not Uploaded"}
                </span>
              </div>
              <div className="file-row compact-upload">
                <input
                  id="csv-file-input"
                  className="visually-hidden-input"
                  type="file"
                  accept=".csv,text/csv"
                  onChange={handleFileChange}
                  disabled={loading || uploading}
                />
                <label
                  className={`file-picker-btn ${loading || uploading ? "disabled" : ""}`}
                  htmlFor="csv-file-input"
                >
                  Choose CSV
                </label>
                <span className="file-chip" title={selectedName}>{selectedName}</span>
              </div>
              <div className="file-meta single-line" title={csvFilePath || selectedName}>
                {csvFilePath ? `Ready: ${selectedName}` : selectedName}
              </div>
            </div>

            <form className="input-form" onSubmit={handleSend}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Type your question..."
                disabled={loading}
              />
              <button type="submit" disabled={loading || !input.trim()}>
                Send
              </button>
            </form>
          </section>
        </div>
      </section>
    </main>
  );
}

export default App;
