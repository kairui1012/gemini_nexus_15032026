const API_URL = "/api/chat";
const UPLOAD_URL = "/api/upload-csv";

export async function uploadCsv(file) {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(UPLOAD_URL, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    const errorText = await response.text();
    let detail = errorText;
    try {
      const parsed = JSON.parse(errorText);
      detail = parsed?.detail || parsed?.message || errorText;
    } catch {
      // Keep raw text when response is not JSON.
    }
    throw new Error(detail || "CSV upload failed");
  }

  return response.json();
}

export async function sendMessage(message, history = [], csvFilePath = null) {
  const response = await fetch(API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ message, history, csv_file_path: csvFilePath })
  });

  if (!response.ok) {
    const errorText = await response.text();
    let detail = errorText;
    try {
      const parsed = JSON.parse(errorText);
      detail = parsed?.detail || parsed?.message || errorText;
    } catch {
      // Keep raw text when response is not JSON.
    }
    throw new Error(detail || "Request failed");
  }

  return response.json();
}
