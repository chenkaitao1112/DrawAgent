const state = {
  selectedFiles: [],
  pendingSelectionNames: [],
  lastUploadBatchId: "",
  checkpointId: null,
  currentSessionId: "",
  workflow: null,
  agents: [],
  hasStarted: false,
  fileWatcher: null,
  uploadInProgress: false,
  lastObservedInputValue: "",
  uploadPoller: null,
  latestPromptAction: null,
  activeRequestKind: null,
  activeRequestController: null,
  activeStopPayload: null,
  activePendingMessage: null,
  activeRequestStartedAt: null,
  activeRequestTimerId: null,
  lightboxReturnFocus: null,
  workflowInfoMessage: null,
  agentInfoMessage: null,
  workflowInfoLoading: false,
  agentInfoLoading: false,
};

const elements = {
  fileUploadForm: document.getElementById("file-upload-form"),
  fileUploadSubmit: document.getElementById("file-upload-submit"),
  filePickerTrigger: document.querySelector(".file-picker-trigger"),
  uploadBatchId: document.getElementById("upload-batch-id"),
  fileInput: document.getElementById("file-input"),
  fileCountChip: document.getElementById("file-count-chip"),
  selectedFiles: document.getElementById("selected-files"),
  fileImportStatus: document.getElementById("file-import-status"),
  promptInput: document.getElementById("prompt-input"),
  chatThread: document.getElementById("chat-thread"),
  threadShell: document.getElementById("thread-shell"),
  refreshWorkflowBtn: document.getElementById("refresh-workflow-btn"),
  loadAgentsBtn: document.getElementById("load-agents-btn"),
  fillDemoBtn: document.getElementById("fill-demo-btn"),
  workflowSummaryBtn: document.getElementById("workflow-summary-btn"),
  generatePromptBtn: document.getElementById("generate-prompt-btn"),
  sendBtn: document.getElementById("send-btn"),
  workflowStateCount: document.getElementById("workflow-state-count"),
  agentCount: document.getElementById("agent-count"),
  backendFileStatus: document.getElementById("backend-file-status"),
  checkpointBadge: document.getElementById("checkpoint-badge"),
  threadStatus: document.getElementById("thread-status"),
  imageLightbox: document.getElementById("image-lightbox"),
  imageLightboxImage: document.getElementById("image-lightbox-image"),
  lightboxCloseBtn: document.getElementById("lightbox-close-btn"),
  lightboxDownloadBtn: document.getElementById("lightbox-download-btn"),
};

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getDownloadExtension(contentType = "", imageUrl = "") {
  const normalizedType = String(contentType).toLowerCase();
  if (normalizedType.includes("jpeg") || normalizedType.includes("jpg")) return "jpg";
  if (normalizedType.includes("webp")) return "webp";
  if (normalizedType.includes("gif")) return "gif";

  const pathMatch = String(imageUrl).match(/\.([a-zA-Z0-9]{2,5})(?:[?#]|$)/);
  return pathMatch?.[1]?.toLowerCase() || "png";
}

function buildImageDownloadName(extension = "png") {
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `drawagent-${timestamp}.${extension}`;
}

async function downloadGeneratedImage(imageUrl, actionButton = null) {
  if (!imageUrl) {
    return;
  }

  const previousText = actionButton?.textContent || "";
  if (actionButton) {
    actionButton.disabled = true;
    actionButton.textContent = "正在准备下载…";
  }

  try {
    const response = await fetch(imageUrl);
    if (!response.ok) {
      throw new Error("image download failed");
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = buildImageDownloadName(getDownloadExtension(blob.type, imageUrl));
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
  } catch (error) {
    const fallbackLink = document.createElement("a");
    fallbackLink.href = imageUrl;
    fallbackLink.download = buildImageDownloadName(getDownloadExtension("", imageUrl));
    fallbackLink.target = "_blank";
    fallbackLink.rel = "noopener";
    document.body.appendChild(fallbackLink);
    fallbackLink.click();
    fallbackLink.remove();
  } finally {
    if (actionButton) {
      actionButton.disabled = false;
      actionButton.textContent = previousText || "下载图片";
    }
  }
}

function openImageLightbox(imageUrl, triggerElement = null) {
  if (!imageUrl || !elements.imageLightbox || !elements.imageLightboxImage) {
    return;
  }

  state.lightboxReturnFocus = triggerElement || document.activeElement;
  elements.imageLightboxImage.src = imageUrl;
  elements.imageLightbox.hidden = false;
  elements.imageLightbox.setAttribute("aria-hidden", "false");
  document.body.classList.add("lightbox-open");
  window.requestAnimationFrame(() => elements.lightboxCloseBtn?.focus());
}

function closeImageLightbox() {
  if (!elements.imageLightbox || elements.imageLightbox.hidden) {
    return;
  }

  elements.imageLightbox.hidden = true;
  elements.imageLightbox.setAttribute("aria-hidden", "true");
  elements.imageLightboxImage?.removeAttribute("src");
  document.body.classList.remove("lightbox-open");

  if (state.lightboxReturnFocus instanceof HTMLElement) {
    state.lightboxReturnFocus.focus();
  }
  state.lightboxReturnFocus = null;
}

function getGeneratedImageFromAction(actionElement) {
  return actionElement?.closest(".image-result-panel")?.querySelector(".generated-image") || null;
}

function getFileIconSvg(className = "") {
  const safeClass = className ? ` ${className}` : "";
  return `
    <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" class="${safeClass.trim()}">
      <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z"></path>
      <path d="M14 2v5h5"></path>
    </svg>
  `;
}

function getFileTypeLabel(fileName) {
  const ext = String(fileName).split(".").pop()?.toLowerCase() || "";

  if (ext === "pdf") return "PDF";
  if (ext === "doc" || ext === "docx") return "Word";
  if (ext === "md") return "Markdown";
  if (ext === "txt") return "Text";

  return ext ? ext.toUpperCase() : "\u6587\u6863";
}

function normalizeSelectedFile(file) {
  if (typeof file === "string") {
    return {
      name: file,
      typeLabel: getFileTypeLabel(file),
      rawFile: null,
      uploaded: false,
    };
  }

  return {
    name: file.name,
    typeLabel: getFileTypeLabel(file.name),
    rawFile: file,
    uploaded: false,
  };
}

function extractNativeSelectionNames() {
  const files = elements.fileInput.files;
  if (files && files.length > 0) {
    return Array.from(files)
      .map((file) => file?.name)
      .filter(Boolean);
  }

  const inputValue = elements.fileInput.value || "";
  if (!inputValue) {
    return [];
  }

  const fileName = inputValue.split(/[/\\\\]/).pop() || inputValue;
  return fileName ? [fileName] : [];
}

function clearPendingSelection() {
  state.pendingSelectionNames = [];
}

function syncPendingSelectionFromState() {
  state.pendingSelectionNames = state.selectedFiles
    .filter((file) => !file.uploaded)
    .map((file) => file.name);
}

function replacePendingFiles(newFiles) {
  const uploadedFiles = state.selectedFiles.filter((file) => file.uploaded);
  const pendingFiles = (newFiles || []).map(normalizeSelectedFile);
  state.selectedFiles = [...uploadedFiles, ...pendingFiles];
  syncPendingSelectionFromState();
}

function replacePendingSelectionNames(fileNames) {
  const uploadedFiles = state.selectedFiles.filter((file) => file.uploaded);
  state.selectedFiles = uploadedFiles;
  state.pendingSelectionNames = Array.from(new Set((fileNames || []).filter(Boolean)));
}

function mergeUploadedFiles(uploadedFiles) {
  const merged = [...state.selectedFiles];

  uploadedFiles.forEach((incomingFile) => {
    const existingIndex = merged.findIndex((file) => file.name === incomingFile.name);
    if (existingIndex >= 0) {
      merged[existingIndex] = {
        ...merged[existingIndex],
        ...incomingFile,
      };
      return;
    }

    merged.push(incomingFile);
  });

  state.selectedFiles = merged;
  syncPendingSelectionFromState();
}

function getVisibleFiles() {
  const visibleFiles = state.selectedFiles.map((file) => ({
    ...file,
    pending: false,
  }));
  const existingNames = new Set(visibleFiles.map((file) => file.name));

  state.pendingSelectionNames.forEach((name) => {
    if (existingNames.has(name)) {
      return;
    }

    visibleFiles.push({
      name,
      typeLabel: getFileTypeLabel(name),
      rawFile: null,
      uploaded: false,
      pending: true,
    });
  });

  return visibleFiles;
}

function setPendingSelection(fileNames, sourceLabel = "\u672c\u5730\u9009\u62e9") {
  replacePendingSelectionNames(fileNames);

  if (state.pendingSelectionNames.length > 0 && state.selectedFiles.length === 0) {
    setConversationStarted();
    setBackendFileStatus("\u5f85\u4e0a\u4f20");
    setLocalFileStatus(
      "is-ready",
      `${sourceLabel}\uff1a\u5df2\u9009\u62e9 ${state.pendingSelectionNames.length} \u4e2a\u9644\u4ef6\uff0c\u70b9\u51fb\u201c\u4e0a\u4f20\u9644\u4ef6\u201d\u540e\u63d0\u4ea4\u5230\u540e\u7aef`
    );
  }

  renderSelectedFiles();
}

function refreshLayoutState() {
  if (state.hasStarted) {
    elements.threadShell.classList.remove("is-idle");
    return;
  }

  elements.threadShell.classList.add("is-idle");
}

function setConversationStarted() {
  if (state.hasStarted) {
    return;
  }

  state.hasStarted = true;
  refreshLayoutState();
}

function setLocalFileStatus(statusClass, text) {
  elements.fileImportStatus.className = `file-import-status ${statusClass}`;
  elements.fileImportStatus.textContent = text;
}

function updateFileCountChip() {
  const count = getVisibleFiles().length;
  elements.fileCountChip.textContent = String(count);
  elements.fileCountChip.classList.toggle("is-empty", count === 0);
}

function setBackendFileStatus(text) {
  elements.backendFileStatus.textContent = text;
}

function createUploadBatchId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }

  return `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function applyUploadedFiles(uploadedFiles, uploadBatchId = "") {
  const incomingFiles = (uploadedFiles || []).map((file) => ({
    name: file.name,
    typeLabel: getFileTypeLabel(file.name),
    rawFile: null,
    uploaded: true,
    saved_path: file.saved_path,
    size_bytes: file.size_bytes,
    content_type: file.content_type,
  }));

  if (uploadBatchId) {
    state.lastUploadBatchId = uploadBatchId;
  }
  mergeUploadedFiles(incomingFiles);
  setConversationStarted();
  renderSelectedFiles();
}

function createClientRequestId() {
  return createUploadBatchId();
}

function stopUploadPolling() {
  if (state.uploadPoller) {
    window.clearInterval(state.uploadPoller);
    state.uploadPoller = null;
  }
}

function startUploadPolling(uploadBatchId) {
  stopUploadPolling();
  let attempts = 0;

  state.uploadPoller = window.setInterval(async () => {
    attempts += 1;
    try {
      const response = await fetch(`/session/upload-status/${encodeURIComponent(uploadBatchId)}`);
      if (!response.ok) {
        return;
      }

      const data = await response.json();
      const uploadedFiles = data.uploaded_files || [];
      if (uploadedFiles.length > 0) {
        state.uploadInProgress = false;
        applyUploadedFiles(uploadedFiles, data.upload_batch_id || uploadBatchId);
        setBackendFileStatus(`\u5df2\u4e0a\u4f20 ${uploadedFiles.length} \u4e2a\u5b9e\u9645\u6587\u4ef6`);
        setLocalFileStatus(
          "is-submitted",
          `\u5df2\u6210\u529f\u4e0a\u4f20 ${uploadedFiles.length} \u4e2a\u6587\u4ef6\uff0c\u73b0\u5728\u53ef\u4ee5\u751f\u6210 Prompt`
        );
        elements.fileUploadForm.reset();
        elements.uploadBatchId.value = "";
        state.lastObservedInputValue = "";
        clearPendingSelection();
        stopUploadPolling();
        return;
      }
    } catch (error) {
      // Keep polling for a short period; transient errors are tolerated.
    }

    if (attempts >= 20) {
      state.uploadInProgress = false;
      setBackendFileStatus("\u672a\u76d1\u6d4b\u5230\u4e0a\u4f20\u7ed3\u679c");
      setLocalFileStatus(
        "is-empty",
        "\u5df2\u63d0\u4ea4\u4e0a\u4f20\uff0c\u4f46\u8f6e\u8be2\u672a\u770b\u5230\u540e\u7aef\u7ed3\u679c\uff0c\u53ef\u80fd\u662f\u5bb9\u5668\u62e6\u622a\u4e86\u539f\u751f\u4e0a\u4f20"
      );
      stopUploadPolling();
    }
  }, 500);
}

function submitNativeUploadFallback(sourceLabel = "\u539f\u751f\u8868\u5355") {
  if (state.uploadInProgress) {
    return false;
  }

  const selectedNames = extractNativeSelectionNames();
  if (selectedNames.length === 0) {
    return false;
  }

  state.uploadInProgress = true;
  const uploadBatchId = createUploadBatchId();
  elements.uploadBatchId.value = uploadBatchId;
  setBackendFileStatus("\u6b63\u5728\u4f7f\u7528\u539f\u751f\u8868\u5355\u4e0a\u4f20\u9644\u4ef6");
  setLocalFileStatus(
    "is-ready",
    `${sourceLabel}\uff1a\u5df2\u9009\u62e9 ${selectedNames.length} \u4e2a\u9644\u4ef6\uff0c\u6b63\u5728\u4f7f\u7528\u6d4f\u89c8\u5668\u539f\u751f\u4e0a\u4f20`
  );
  startUploadPolling(uploadBatchId);
  elements.fileUploadForm.submit();
  return true;
}

async function uploadFilesFromForm(inputLabel = "") {
  if (state.uploadInProgress) {
    return false;
  }

  const formData = new FormData(elements.fileUploadForm);
  const nativeFiles = formData.getAll("files");
  if (!nativeFiles.length) {
    return false;
  }

  state.uploadInProgress = true;
  setBackendFileStatus(`\u6b63\u5728\u4e0a\u4f20 ${nativeFiles.length} \u4e2a\u5b9e\u9645\u6587\u4ef6`);
  setLocalFileStatus(
    "is-ready",
    `${inputLabel || "\u539f\u751f\u8f93\u5165\u6846"}\uff1a\u5df2\u53d1\u73b0\u53ef\u63d0\u4ea4\u7684\u9644\u4ef6\uff0c\u6b63\u5728\u4ea4\u7ed9\u540e\u7aef`
  );

  try {
    const response = await fetch("/session/upload-files", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error("upload failed");
    }

    const data = await response.json();
    applyUploadedFiles(data.uploaded_files || [], data.upload_batch_id || "");
    setBackendFileStatus(
      data.uploaded_files?.length > 0
        ? `\u5df2\u4e0a\u4f20 ${data.uploaded_files.length} \u4e2a\u5b9e\u9645\u6587\u4ef6`
        : "\u672c\u6b21\u672a\u4e0a\u4f20\u9644\u4ef6"
    );
    setLocalFileStatus(
      "is-submitted",
      data.uploaded_files?.length > 0
        ? `\u5df2\u6210\u529f\u4e0a\u4f20 ${data.uploaded_files.length} \u4e2a\u6587\u4ef6\uff0c\u53ef\u4ee5\u76f4\u63a5\u751f\u6210 Prompt`
        : "\u672c\u6b21\u672a\u4e0a\u4f20\u9644\u4ef6"
    );
    elements.fileUploadForm.reset();
    state.lastObservedInputValue = "";
    return true;
  } catch (error) {
    setBackendFileStatus("\u9644\u4ef6\u4e0a\u4f20\u5931\u8d25");
    setLocalFileStatus("is-empty", "\u5df2\u9009\u4e2d\u9644\u4ef6\uff0c\u4f46\u4ea4\u7ed9\u540e\u7aef\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5");
    return false;
  } finally {
    state.uploadInProgress = false;
  }
}

function syncFilesFromInput(eventLabel = "\u8f6e\u8be2\u68c0\u6d4b", allowEmptyMessage = false) {
  const files = elements.fileInput.files;
  const inputValue = elements.fileInput.value || "";

  if (files && files.length > 0) {
    return handleNativeSelection(elements.fileInput, eventLabel);
  }

  if (inputValue && inputValue !== state.lastObservedInputValue) {
    return handleNativeSelection(elements.fileInput, eventLabel);
  }

  if (allowEmptyMessage && state.selectedFiles.length === 0 && inputValue) {
    const fileName = inputValue.split(/[/\\\\]/).pop() || inputValue;
    setPendingSelection([fileName], eventLabel);
    setLocalFileStatus(
      "is-ready",
      `${eventLabel}\uff1a\u5df2\u9009\u4e2d ${fileName}\uff0c\u53ef\u4ee5\u70b9\u51fb\u201c\u4e0a\u4f20\u9644\u4ef6\u201d\u7ee7\u7eed`
    );
  }

  return false;
}

function startFileInputWatcher(reason) {
  if (state.fileWatcher) {
    window.clearInterval(state.fileWatcher);
  }

  let tick = 0;
  state.fileWatcher = window.setInterval(() => {
    tick += 1;
    const matched = syncFilesFromInput(`${reason} / ${tick}`, tick >= 6);
    if (matched || tick >= 20) {
      window.clearInterval(state.fileWatcher);
      state.fileWatcher = null;
    }
  }, 150);
}

function scheduleNativeFileRead(eventLabel, allowEmptyMessage = false) {
  const delays = [0, 80, 220];
  delays.forEach((delay) => {
    window.setTimeout(() => {
      const matched = syncFilesFromInput(eventLabel, allowEmptyMessage && delay === delays[delays.length - 1]);
      if (!matched && delay === delays[delays.length - 1]) {
        startFileInputWatcher(eventLabel);
      }
    }, delay);
  });
}

function handleNativeFiles(fileList, options = {}) {
  const files = Array.from(fileList || []);
  const allowEmptyMessage = options.allowEmptyMessage ?? false;
  const sourceLabel = options.sourceLabel || "\u539f\u751f\u9009\u62e9";

  if (files.length === 0) {
    if (allowEmptyMessage && state.selectedFiles.length === 0) {
      setLocalFileStatus("is-empty", "\u8fd9\u6b21\u6ca1\u6709\u9009\u4e2d\u4efb\u4f55\u9644\u4ef6");
    }
    return;
  }

  replacePendingFiles(files);
  state.lastObservedInputValue = "";
  setConversationStarted();
  renderSelectedFiles();
  setBackendFileStatus("\u6b63\u5728\u4e0a\u4f20");
  setLocalFileStatus(
    "is-ready",
    `\u5df2\u9009\u4e2d ${files.length} \u4e2a\u9644\u4ef6\uff0c\u6b63\u5728\u81ea\u52a8\u4e0a\u4f20\u5230\u540e\u7aef`
  );
  if (!state.uploadInProgress) {
    const submitted = submitNativeUploadFallback(`${sourceLabel} / auto`);
    if (!submitted) {
      uploadSelectedFiles().catch(() => {
        setBackendFileStatus("\u9644\u4ef6\u4e0a\u4f20\u5931\u8d25");
        setLocalFileStatus("is-empty", "\u9644\u4ef6\u5df2\u9009\u4e2d\uff0c\u4f46\u81ea\u52a8\u4e0a\u4f20\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5");
      });
    }
  }
}

function handleNativeSelection(input = elements.fileInput, eventLabel = "\u539f\u751f\u9009\u62e9") {
  if (!input) {
    return false;
  }

  const files = input.files;
  if (files && files.length > 0) {
    handleNativeFiles(files, { sourceLabel: eventLabel });
    return true;
  }

  const names = extractNativeSelectionNames();
  if (names.length > 0) {
    setPendingSelection(names, eventLabel);
    setConversationStarted();
    renderSelectedFiles();
    setBackendFileStatus("\u6b63\u5728\u4e0a\u4f20");
    setLocalFileStatus(
      "is-ready",
      `\u5df2\u9009\u4e2d ${names.length} \u4e2a\u9644\u4ef6\uff0c\u6b63\u5728\u81ea\u52a8\u4e0a\u4f20\u5230\u540e\u7aef`
    );
    state.lastObservedInputValue = input.value || "";
    if (!state.uploadInProgress) {
      const submitted = submitNativeUploadFallback(`${eventLabel} / auto`);
      if (!submitted) {
        uploadFilesFromForm(eventLabel).catch(() => {
          setBackendFileStatus("\u9644\u4ef6\u4e0a\u4f20\u5931\u8d25");
          setLocalFileStatus("is-empty", "\u9644\u4ef6\u5df2\u9009\u4e2d\uff0c\u4f46\u81ea\u52a8\u4e0a\u4f20\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5");
        });
      }
    }
    return true;
  }

  return false;
}

function renderSelectedFiles() {
  const visibleFiles = getVisibleFiles();
  elements.selectedFiles.innerHTML = "";
  updateFileCountChip();

  if (visibleFiles.length === 0) {
    setLocalFileStatus("is-empty", "\u672a\u9009\u62e9\u9644\u4ef6");
    return;
  }

  setLocalFileStatus(
    "is-ready",
    visibleFiles.every((file) => file.uploaded)
      ? `\u5df2\u4e0a\u4f20 ${visibleFiles.length} \u4e2a\u6587\u4ef6\u5230\u540e\u7aef`
      : `\u5df2\u9009\u4e2d ${visibleFiles.length} \u4e2a\u9644\u4ef6\uff0c\u53ef\u4ee5\u7ee7\u7eed\u4e0a\u4f20\u6216\u76f4\u63a5\u63d0\u4ea4`
  );

  visibleFiles.forEach((file) => {
    const card = document.createElement("article");
    card.className = "file-card";
    const fileMeta = file.uploaded
      ? `${file.typeLabel} \u00b7 \u5df2\u4e0a\u4f20`
      : `${file.typeLabel} \u00b7 \u5f85\u4e0a\u4f20`;
    card.innerHTML = `
      <div class="file-card-icon">${getFileIconSvg("file-card-icon-svg")}</div>
      <div class="file-card-body">
        <div class="file-card-title">${escapeHtml(file.name)}</div>
        <div class="file-card-meta">${escapeHtml(fileMeta)}</div>
      </div>
      <button
        class="file-card-remove"
        type="button"
        data-name="${escapeHtml(file.name)}"
        title="\u79fb\u9664\u8be5\u6587\u4ef6"
        aria-label="\u79fb\u9664\u8be5\u6587\u4ef6"
      >
        \u00d7
      </button>
    `;
    elements.selectedFiles.appendChild(card);
  });
}

function buildBubbleAttachments(files) {
  const safeFiles = (files || []).filter(Boolean);
  if (!safeFiles.length) {
    return "";
  }

  const cards = safeFiles
    .map((file) => {
      const fileName = escapeHtml(file.name || "");
      const typeLabel = escapeHtml(getFileTypeLabel(file.name || ""));
      return `
        <div class="bubble-attachment">
          <div class="bubble-attachment-icon">${getFileIconSvg("bubble-attachment-icon-svg")}</div>
          <div>
            <div class="bubble-attachment-title">${fileName}</div>
            <div class="bubble-attachment-meta">${typeLabel}</div>
          </div>
        </div>
      `;
    })
    .join("");

  return `<div class="bubble-attachments">${cards}</div>`;
}

function buildUserMessageHtml(text, files = []) {
  const attachmentsHtml = buildBubbleAttachments(files);
  const messageText = escapeHtml(text || "");

  if (!messageText) {
    return attachmentsHtml || '<div class="bubble-text">请基于附件继续处理</div>';
  }

  return `${attachmentsHtml}<div class="bubble-text">${messageText}</div>`;
}

function resetComposerFiles() {
  state.selectedFiles = [];
  clearPendingSelection();
  elements.fileUploadForm.reset();
  elements.uploadBatchId.value = "";
  state.lastObservedInputValue = "";
  renderSelectedFiles();
  setBackendFileStatus("\u672a\u63d0\u4ea4");
}

function formatElapsedRuntime(ms) {
  const totalSeconds = Math.max(0, Math.floor((ms || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }

  if (minutes > 0) {
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }

  return `${seconds}s`;
}

function buildRuntimeLabel(startedAt = Date.now()) {
  return `已处理 ${formatElapsedRuntime(Date.now() - startedAt)}`;
}

function resizePromptInput() {
  const input = elements.promptInput;
  if (!input) {
    return;
  }
  input.style.height = "auto";
  const maxHeight = 220;
  const nextHeight = Math.min(Math.max(input.scrollHeight, 44), maxHeight);
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}

function clearPromptInput() {
  elements.promptInput.value = "";
  resizePromptInput();
}

function updatePendingRuntimeLabel() {
  if (!state.activePendingMessage || !state.activeRequestStartedAt) {
    return;
  }

  const runtimeNode = state.activePendingMessage.querySelector('[data-role="assistant-runtime"]');
  if (!runtimeNode) {
    return;
  }

  runtimeNode.textContent = buildRuntimeLabel(state.activeRequestStartedAt);
}

function stopActiveRuntimeTimer() {
  if (state.activeRequestTimerId) {
    window.clearInterval(state.activeRequestTimerId);
    state.activeRequestTimerId = null;
  }
}


function appendMessage(role, meta, contentHtml, options = {}) {
  setConversationStarted();
  const article = document.createElement("article");
  const actionHtml = options.actionHtml || "";
  const runtimeHtml = options.runtimeLabel
    ? `<div class="message-runtime">${escapeHtml(options.runtimeLabel)}</div>`
    : "";
  const body = `
    <div class="message-body">
      <div class="message-meta">${escapeHtml(meta)}</div>
      <div class="message-content-row">
        <div class="bubble">${contentHtml}</div>
        ${actionHtml}
      </div>
      ${runtimeHtml}
    </div>
  `;

  article.className = `message ${role}`;
  article.innerHTML = body;

  elements.chatThread.appendChild(article);
  window.requestAnimationFrame(() => {
    article.scrollIntoView({ behavior: "auto", block: "end" });
  });
  return article;
}

function appendUserMessage(meta, text, files = []) {
  setConversationStarted();
  const article = document.createElement("article");
  const attachmentsHtml = buildBubbleAttachments(files);
  const messageText = escapeHtml(text || "");
  const textBubble = messageText ? `<div class="bubble"><div class="bubble-text">${messageText}</div></div>` : "";
  const body = `
    <div class="message-body">
      <div class="message-meta">${escapeHtml(meta)}</div>
      ${attachmentsHtml}
      ${textBubble}
    </div>
  `;

  article.className = "message user";
  article.innerHTML = body;
  elements.chatThread.appendChild(article);
  window.requestAnimationFrame(() => {
    article.scrollIntoView({ behavior: "auto", block: "end" });
  });
  return article;
}


function setThreadStatus(text) {
  elements.threadStatus.textContent = text;
}

function setCheckpoint(text) {
  elements.checkpointBadge.textContent = text;
}

function updateWorkflowSummary(data) {
  state.workflow = data;
  const states = data.states || [];
  elements.workflowStateCount.textContent = `${states.length} \u4e2a\u72b6\u6001`;
}

function updateAgentSummary(data) {
  state.agents = data.agents || [];
  elements.agentCount.textContent = `${state.agents.length} \u4e2a`;
}

function formatWorkflowBubble() {
  if (!state.workflow) {
    return [
      '<span class="assistant-card-title">\u5de5\u4f5c\u6d41\u5c1a\u672a\u52a0\u8f7d</span>',
      "<div>\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002</div>",
    ].join("");
  }

  return [
    '<span class="assistant-card-title">一张科研图是怎样完成的</span>',
    '<div>drawAgent 会按下面四步推进。需要你确认时，我会明确告诉你下一步该点哪里。</div>',
    '<div class="orchestration-summary">',
    '<div class="skill-summary-card"><span class="skill-label">工作方式</span><strong>多角色协作绘图</strong><p>系统会自动完成论文理解、画面设计和提示词检查，不需要你阅读内部执行链。</p></div>',
    '<ol class="workflow-steps">',
    '<li class="workflow-step"><span class="workflow-step-index">1</span><div><strong>理解论文与绘图要求</strong><p>提取研究方法、核心模块、逻辑关系和你的视觉偏好。</p></div></li>',
    '<li class="workflow-step"><span class="workflow-step-index">2</span><div><strong>设计画面结构</strong><p>把论文逻辑整理成适合阅读的分区、层级与信息流。</p></div></li>',
    '<li class="workflow-step"><span class="workflow-step-index">3</span><div><strong>生成并检查提示词</strong><p>形成完整的生图描述，并检查内容是否准确、表达是否清晰。</p></div></li>',
    '<li class="workflow-step"><span class="workflow-step-index">4</span><div><strong>确认后生成图片</strong><p>你确认提示词后开始生图；不满意可以继续提出修改要求。</p></div></li>',
    '</ol>',
    "</div>",
  ].join("");
}

function upsertInfoMessage(stateKey, meta, contentHtml) {
  const currentMessage = state[stateKey];
  if (currentMessage?.isConnected) {
    const bubble = currentMessage.querySelector(".bubble");
    if (bubble) {
      bubble.innerHTML = contentHtml;
    }
    return currentMessage;
  }

  const message = appendMessage("agent", meta, contentHtml);
  state[stateKey] = message;
  return message;
}

function formatAgentsBubble() {
  if (!state.agents.length) {
    return [
      '<span class="assistant-card-title">Agents \u5c1a\u672a\u52a0\u8f7d</span>',
      "<div>\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002</div>",
    ].join("");
  }

  const lines = state.agents
    .map((agent) => {
      const name = escapeHtml(agent.name || "-");
      const role = escapeHtml(agent.role || "-");
      const model = escapeHtml(agent.model || "-");
      return `${name} -> ${role} -> ${model}`;
    })
    .join("<br>");

  return [
    '<span class="assistant-card-title">\u5f53\u524d\u5df2\u6ce8\u518c\u7684 Agents</span>',
    `<div class="preview-panel">${lines}</div>`,
  ].join("");
}

const SKILL_PRESENTATIONS = {
  scientific_diagram: {
    name: "科研方法图",
    description: "把论文中的方法逻辑、核心模块和信息流转化为清晰的科研架构图。",
  },
  grant_diagram: {
    name: "基金申请图",
    description: "把研究目标、技术路线和预期成果整理为适合基金申请材料的图示。",
  },
  document_ingestion_routing: {
    name: "文档理解与分流",
    description: "读取文档内容，判断任务类型，并选择最合适的后续绘图流程。",
  },
};

function getSkillPresentation(skillId = "") {
  if (SKILL_PRESENTATIONS[skillId]) {
    return SKILL_PRESENTATIONS[skillId];
  }

  return {
    name: "科研绘图",
    description: "根据你的文档和绘图要求，选择合适的智能体协作完成任务。",
  };
}

function buildUserFacingWorkflow(stages = []) {
  const roles = new Set((stages || []).map((stage) => stage.stage_role || ""));
  const steps = [];

  if (roles.has("logic") || roles.has("style")) {
    steps.push({
      title: "理解论文与绘图要求",
      description: "提取研究方法、核心模块和逻辑关系，同时识别学科、会议规范与视觉偏好。",
    });
  }
  if (roles.has("visual_mapping")) {
    steps.push({
      title: "设计画面结构",
      description: "把论文逻辑映射为可视化布局，确定模块层级、连接关系和阅读顺序。",
    });
  }
  if (roles.has("final_prompt")) {
    steps.push({
      title: "生成并检查绘图提示词",
      description: "整合内容与视觉方案，生成英文绘图提示词，并进行质量审查和必要修订。",
    });
  }
  if (roles.has("image")) {
    steps.push({
      title: "确认后生成图片",
      description: "提示词准备完成后先交给你确认；只有你点击开始生图，系统才会生成最终图片。",
    });
  }

  if (!steps.length) {
    steps.push(
      {
        title: "理解任务",
        description: "读取你的材料，明确需要表达的内容与绘图目标。",
      },
      {
        title: "形成绘图方案",
        description: "组织信息结构并生成经过检查的绘图提示词。",
      },
      {
        title: "确认后生成图片",
        description: "把方案交给你确认，再开始生成最终图片。",
      }
    );
  }

  return steps;
}

function formatPromptBubble(result) {
  const rawPayloadValue = result.payload_final?.value || "";
  const workflowState = result.workflow_state || {};
  const finalResponse = workflowState.final_response || {};
  const payload =
    typeof rawPayloadValue === "string"
      ? {
          english_prompt: rawPayloadValue,
          chinese_explanation: finalResponse.chinese_explanation || "",
          review_approved: finalResponse.review_approved,
          review_warning: finalResponse.review_warning || "",
          latest_review_feedback: finalResponse.latest_review_feedback || {},
        }
      : rawPayloadValue || {};
  if (result.stage === "stopped") {
    return [
      '<span class="assistant-card-title">流程已停止</span>',
      `<div>${escapeHtml(finalResponse.chinese_explanation || "本轮流程已停止。")}</div>`,
    ].join("");
  }
  if (result.stage === "review_failed") {
    const latestReview = finalResponse.latest_review_feedback || {};
    const serviceErrorCodes = new Set([
      "timeout",
      "rate_limited",
      "provider_unavailable",
      "invalid_provider_response",
      "auth_failed",
      "runtime_error",
    ]);
    const isServiceFailure =
      Boolean(latestReview.transient_error) || serviceErrorCodes.has(latestReview.error_code || "");
    const issues = (latestReview.issues || [])
      .map((item) => `- ${escapeHtml(item)}`)
      .join("<br>");
    return [
      `<span class="assistant-card-title">${isServiceFailure ? "模型服务调用失败" : "内容审查未通过"}</span>`,
      `<div>${escapeHtml(finalResponse.chinese_explanation || (isServiceFailure ? "模型服务暂时无法完成请求。" : "内容审查未通过，流程已停止。"))}</div>`,
      issues ? `<div class="preview-panel"><strong>${isServiceFailure ? "技术详情" : "审查意见"}</strong><br>${issues}</div>` : "",
    ].join("");
  }
  if (result.stage === "routing_instruction") {
    return [
      '<span class="assistant-card-title">已识别当前意图</span>',
      `<div>${escapeHtml(finalResponse.chinese_explanation || "已完成入口意图识别。")}</div>`,
      '<div class="preview-panel">',
      "<strong>Intent</strong><br>",
      `${escapeHtml(finalResponse.detected_intent || workflowState.detected_intent || "-")}<br><br>`,
      "<strong>Reason</strong><br>",
      `${escapeHtml(finalResponse.routing_reason || workflowState.routing_reason || "-")}`,
      "</div>",
    ].join("");
  }
  if (result.stage === "clarification_needed") {
    const collectedValues = finalResponse.collected_values || {};
    const missingFields = finalResponse.missing_fields || [];
    const collectedLines = [
      `primary_discipline: ${escapeHtml(collectedValues.primary_discipline || "-")}`,
      `conference_name: ${escapeHtml(collectedValues.conference_name || "-")}`,
      `user_preferences: ${escapeHtml(collectedValues.user_preferences || "-")}`,
    ].join("<br>");
    const missingLines = missingFields.length
      ? missingFields.map((item) => `- ${escapeHtml(item)}`).join("<br>")
      : "-";
    return [
      '<span class="assistant-card-title">需要补充澄清信息</span>',
      `<div>${escapeHtml(finalResponse.chinese_explanation || "请补充继续生成科研绘图所需的信息。")}</div>`,
      '<div class="preview-panel">',
      "<strong>缺失字段</strong><br>",
      `${missingLines}<br><br>`,
      "<strong>当前已收集</strong><br>",
      `${collectedLines}`,
      "</div>",
    ].join("");
  }
  if (result.stage === "awaiting_orchestration_confirmation") {
    const plan = finalResponse.orchestration_plan || workflowState.orchestration_plan || {};
    const stages = plan.stages || [];
    const selectedSkillId = plan.selected_skill || result.selected_skill || "";
    const skill = getSkillPresentation(selectedSkillId);
    const userFacingSteps = buildUserFacingWorkflow(stages);
    const stepCards = userFacingSteps
      .map(
        (step, index) => `
          <li class="workflow-step">
            <span class="workflow-step-number">${index + 1}</span>
            <div class="workflow-step-content">
              <strong>${escapeHtml(step.title)}</strong>
              <span>${escapeHtml(step.description)}</span>
            </div>
          </li>
        `
      )
      .join("");
    return [
      '<span class="assistant-card-title">我已经安排好处理流程</span>',
      '<div>本次任务会由多个智能体按步骤协作完成。你只需要确认流程，后面的结果会自动传递到下一步。</div>',
      '<div class="orchestration-summary">',
      '<div class="skill-summary-card">',
      '<span class="skill-summary-label">本次使用的 Skill</span>',
      `<strong class="skill-summary-name">${escapeHtml(skill.name)}</strong>`,
      `<span class="skill-summary-description">${escapeHtml(skill.description)}</span>`,
      "</div>",
      `<ol class="workflow-steps">${stepCards}</ol>`,
      "</div>",
    ].join("");
  }
  const promptText = escapeHtml(payload.english_prompt || "");
  const reviewApproved = payload.review_approved !== false;
  const reviewFailed = !reviewApproved || result.stage === "prompt_review_failed";
  const criticReviews = payload.critic_reviews || [];
  const latestReview =
    payload.latest_review_feedback ||
    criticReviews[criticReviews.length - 1] ||
    criticReviews.find((item) => item.review_phase === "final_prompt_review") ||
    {};
  const issues = (latestReview.issues || [])
    .map((item) => `- ${escapeHtml(item)}`)
    .join("<br>");
  const reviewWarning = reviewFailed
    ? escapeHtml(
        payload.review_warning ||
          "\u5f53\u524d Prompt \u8fd8\u672a\u901a\u8fc7\u5ba1\u67e5\uff0c\u4f46\u7cfb\u7edf\u5df2\u5141\u8bb8\u4f60\u7ee7\u7eed\u51fa\u56fe\uff0c\u8bf7\u81ea\u884c\u68c0\u67e5\u3002"
      )
    : "";
  const isRevision = Boolean(finalResponse.revision_scope);
  const changeSummary = escapeHtml(finalResponse.change_summary || "");

  return [
    `<span class="assistant-card-title">${
      reviewFailed
        ? isRevision
          ? "提示词已修改，请先检查"
          : "提示词已生成，请先检查"
        : isRevision
          ? "我已经按你的要求修改了提示词"
          : "我已经生成好了绘图提示词"
    }</span>`,
    reviewFailed
      ? `<div>${reviewWarning} 你可以在输入框写下修改要求，再点击下方“修改提示词”，我会重新生成。</div>`
      : isRevision
        ? `<div>${changeSummary || "修改要求已经融合进完整提示词，并完成局部检查。"} 如果还想调整，可以继续直接发送；确认无误后点击“开始生图”。</div>`
        : '<div>我已经根据论文内容完成提示词生成和审查。点击下方“开始生图”后，我将使用这份提示词生成图片；如果还想调整，直接在输入框写下要求并发送即可。</div>',
    '<details class="prompt-details">',
    '<summary>查看英文绘图提示词</summary>',
    `<div class="prompt-details-body">${promptText || "暂无内容"}</div>`,
    "</details>",
    reviewFailed && issues
      ? `<details class="prompt-details"><summary>查看审查建议</summary><div class="prompt-details-body">${issues}</div></details>`
      : "",
  ].join("");
}

function formatImageStageBubble(result) {
  const notImplemented = result.stage === "image_generation_not_implemented";
  const stopped = result.stage === "stopped";
  const imageUrl = result.image_url ? escapeHtml(result.image_url) : "";
  const revisedPrompt = result.revised_prompt ? escapeHtml(result.revised_prompt) : "";
  const imageMeta = result.image_result?.value || {};
  const providerDurationMs = Number(
    result.provider_request?.provider_duration_ms || imageMeta.provider_duration_ms || 0
  );
  const providerFailed =
    revisedPrompt.includes("fallback used:") ||
    revisedPrompt.includes("403 Forbidden") ||
    revisedPrompt.includes("429 Too Many Requests");
  const failed =
    result.stage === "image_generation_failed" ||
    stopped ||
    (!imageUrl && (providerFailed || (!notImplemented && !stopped)));

  if (failed || notImplemented || stopped) {
    return [
      `<span class="assistant-card-title">${
        stopped ? "本次生图已停止" : notImplemented ? "暂时无法开始生图" : "图片生成失败"
      }</span>`,
      `<div>${escapeHtml(
        result.message ||
          (stopped ? "你可以修改要求后重新开始。" : "请稍后重试，或修改提示词后再次生成。")
      )}</div>`,
    ].join("");
  }

  const completedAt = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
  const durationLabel =
    providerDurationMs > 0
      ? `${Math.max(0.1, providerDurationMs / 1000).toFixed(1)} 秒`
      : "";

  return [
    '<span class="assistant-card-title">图片已经生成完成</span>',
    '<div>我已经完成本次生图，你可以直接查看或保存下面的图片。</div>',
    '<div class="result-meta">',
    `<span class="result-meta-chip">完成时间 ${escapeHtml(completedAt)}</span>`,
    durationLabel ? `<span class="result-meta-chip">生成耗时 ${escapeHtml(durationLabel)}</span>` : "",
    "</div>",
    imageUrl
      ? [
          '<div class="image-result-panel">',
          '<div class="image-result-toolbar">',
          '<span class="image-result-toolbar-copy">双击图片可以放大查看</span>',
          '<div class="image-result-actions">',
          '<button class="image-result-action" type="button" data-role="view-generated-image">查看大图</button>',
          '<button class="image-result-action primary" type="button" data-role="download-generated-image">下载图片</button>',
          "</div>",
          "</div>",
          `<img class="generated-image" src="${imageUrl}" alt="生成图片" tabindex="0" role="button" aria-label="双击或按回车键放大查看生成图片">`,
          '<div class="image-result-hint">双击图片放大 · 按 Esc 关闭大图</div>',
          "</div>",
        ].join("")
      : "",
  ].join("");
}

async function loadWorkflow(showBubble = false) {
  if (state.workflowInfoLoading) {
    return;
  }

  state.workflowInfoLoading = true;
  elements.refreshWorkflowBtn.disabled = true;
  elements.workflowSummaryBtn.disabled = true;

  try {
    const response = await fetch("/workflow");
    const data = await response.json();
    updateWorkflowSummary(data);

    if (showBubble) {
      upsertInfoMessage("workflowInfoMessage", "drawAgent · 流程说明", formatWorkflowBubble());
    }
  } finally {
    state.workflowInfoLoading = false;
    elements.refreshWorkflowBtn.disabled = false;
    elements.workflowSummaryBtn.disabled = false;
  }
}

async function loadAgents(showBubble = false) {
  if (state.agentInfoLoading) {
    return;
  }

  state.agentInfoLoading = true;
  elements.loadAgentsBtn.disabled = true;

  try {
    const response = await fetch("/agents");
    const data = await response.json();
    updateAgentSummary(data);

    if (showBubble) {
      upsertInfoMessage("agentInfoMessage", "drawAgent · Agent 配置", formatAgentsBubble());
    }
  } finally {
    state.agentInfoLoading = false;
    elements.loadAgentsBtn.disabled = false;
  }
}


function fillDemoPrompt() {
  elements.promptInput.value =
    "\u8bf7\u5e2e\u6211\u7ed8\u5236\u4e00\u4efd\u8be5\u8bba\u6587\u7684\u6846\u67b6\u56fe\uff0c\u7a81\u51fa\u8f93\u5165\u5904\u7406\u3001\u53cc\u6ce8\u610f\u529b\u5bf9\u6bd4\u7ed3\u6784\u3001\u8868\u793a\u5dee\u5f02\u8ba1\u7b97\u548c\u6700\u7ec8\u5f02\u5e38\u5224\u5b9a\u3002";
  resizePromptInput();

  if (state.selectedFiles.length === 0) {
    state.selectedFiles = [
      normalizeSelectedFile("Dual Attention Contrastive Representation Learning for Time Series Anomaly Detection.pdf"),
    ];
    setConversationStarted();
    renderSelectedFiles();
  }
}


function bindEvents() {
  window.drawAgentNativeSelect = (input) => handleNativeSelection(input, "\u539f\u751f onchange");

  elements.fileInput.addEventListener("click", () => {
    setLocalFileStatus("is-empty", "\u5df2\u6253\u5f00\u6587\u4ef6\u9009\u62e9\u5668\uff0c\u8bf7\u9009\u62e9\u8981\u5bfc\u5165\u7684 PDF \u6216\u6587\u6863");
    startFileInputWatcher("click");
  });

  elements.fileUploadForm.addEventListener("submit", async (event) => {
    const pendingRawFiles = state.selectedFiles.filter((file) => file.rawFile && !file.uploaded);
    const selectedNames = extractNativeSelectionNames();
    if (pendingRawFiles.length === 0 && selectedNames.length === 0) {
      event.preventDefault();
      setBackendFileStatus("\u672a\u63d0\u4ea4");
      setLocalFileStatus("is-empty", "\u8bf7\u5148\u9009\u62e9\u8981\u4e0a\u4f20\u7684\u9644\u4ef6");
      return;
    }

    event.preventDefault();
    if (elements.fileInput.files && elements.fileInput.files.length > 0) {
      handleNativeFiles(elements.fileInput.files);
    }

    const uploadedByFetch =
      state.selectedFiles.some((file) => file.rawFile && !file.uploaded)
        ? await uploadSelectedFiles()
        : await uploadFilesFromForm("\u624b\u52a8\u4e0a\u4f20");
    if (uploadedByFetch) {
      return;
    }

    state.uploadInProgress = true;
    const uploadBatchId = createUploadBatchId();
    elements.uploadBatchId.value = uploadBatchId;
    setBackendFileStatus("\u6b63\u5728\u4f7f\u7528\u539f\u751f\u8868\u5355\u4e0a\u4f20\u9644\u4ef6");
    setLocalFileStatus(
      "is-ready",
      "\u5982\u679c\u5f53\u524d\u5bb9\u5668\u4e0d\u4f1a\u628a FileList \u66b4\u9732\u7ed9 JS\uff0c\u8fd9\u4e2a\u6309\u94ae\u4ecd\u53ef\u4ee5\u76f4\u63a5\u901a\u8fc7\u6d4f\u89c8\u5668\u539f\u751f\u4e0a\u4f20\u6587\u4ef6"
    );
    startUploadPolling(uploadBatchId);
    elements.fileUploadForm.submit();
  });

  elements.fileInput.addEventListener("focus", () => {
    startFileInputWatcher("focus");
  });

  elements.filePickerTrigger?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    elements.fileInput.click();
  });

  elements.fileInput.addEventListener("change", () => {
    handleNativeSelection(elements.fileInput, "change \u4e8b\u4ef6");
  });

  elements.fileInput.addEventListener("input", () => {
    handleNativeSelection(elements.fileInput, "input \u4e8b\u4ef6");
  });

  elements.fileInput.addEventListener("blur", () => {
    handleNativeSelection(elements.fileInput, "blur \u4e8b\u4ef6");
  });

  elements.selectedFiles.addEventListener("click", (event) => {
    const button = event.target.closest(".file-card-remove");
    if (!button) {
      return;
    }

    const fileName = button.dataset.name || "";
    state.selectedFiles = state.selectedFiles.filter((file) => file.name !== fileName);
    state.pendingSelectionNames = state.pendingSelectionNames.filter((name) => name !== fileName);
    if (state.selectedFiles.length === 0 && state.pendingSelectionNames.length === 0) {
      elements.fileUploadForm.reset();
      state.lastObservedInputValue = "";
    }
    renderSelectedFiles();
    if (state.selectedFiles.length === 0 && state.pendingSelectionNames.length === 0) {
      setBackendFileStatus("\u672a\u63d0\u4ea4");
    }
  });

  elements.chatThread.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }

    const viewButton = target.closest('[data-role="view-generated-image"]');
    if (viewButton) {
      const image = getGeneratedImageFromAction(viewButton);
      openImageLightbox(image?.currentSrc || image?.src || "", viewButton);
      return;
    }

    const downloadButton = target.closest('[data-role="download-generated-image"]');
    if (downloadButton) {
      const image = getGeneratedImageFromAction(downloadButton);
      downloadGeneratedImage(image?.currentSrc || image?.src || "", downloadButton);
    }
  });

  elements.chatThread.addEventListener("dblclick", (event) => {
    const image = event.target instanceof Element ? event.target.closest(".generated-image") : null;
    if (!image) {
      return;
    }
    openImageLightbox(image.currentSrc || image.src || "", image);
  });

  elements.chatThread.addEventListener("keydown", (event) => {
    const image = event.target instanceof Element ? event.target.closest(".generated-image") : null;
    if (!image || (event.key !== "Enter" && event.key !== " ")) {
      return;
    }
    event.preventDefault();
    openImageLightbox(image.currentSrc || image.src || "", image);
  });

  elements.imageLightbox?.addEventListener("click", (event) => {
    const closeTarget =
      event.target instanceof Element
        ? event.target.closest('[data-role="close-image-lightbox"]')
        : null;
    if (closeTarget) {
      closeImageLightbox();
    }
  });

  elements.lightboxCloseBtn?.addEventListener("click", closeImageLightbox);
  elements.lightboxDownloadBtn?.addEventListener("click", () => {
    downloadGeneratedImage(elements.imageLightboxImage?.currentSrc || "", elements.lightboxDownloadBtn);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.imageLightbox?.hidden) {
      event.preventDefault();
      closeImageLightbox();
    }
  });

  elements.fillDemoBtn.addEventListener("click", fillDemoPrompt);
  elements.refreshWorkflowBtn.addEventListener("click", () => loadWorkflow(true));
  elements.loadAgentsBtn.addEventListener("click", () => loadAgents(true));
  elements.workflowSummaryBtn.addEventListener("click", () => loadWorkflow(true));
  elements.generatePromptBtn.addEventListener("click", generatePrompt);
  elements.promptInput.addEventListener("input", resizePromptInput);
  elements.sendBtn.addEventListener("click", () => {
    if (state.activeRequestController) {
      stopCurrentRequest();
      return;
    }

    submitComposerInput();
  });

  elements.promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitComposerInput();
    }
  });

  window.addEventListener("message", (event) => {
    if (event.data?.type !== "drawagent-upload-result") {
      return;
    }

    state.uploadInProgress = false;
    const uploadBatchId = event.data?.payload?.upload_batch_id || "";
    const uploadedFiles = event.data?.payload?.uploaded_files || [];
    stopUploadPolling();
    applyUploadedFiles(uploadedFiles, uploadBatchId);
    setBackendFileStatus(
      uploadedFiles.length > 0
        ? `\u5df2\u901a\u8fc7\u539f\u751f\u8868\u5355\u4e0a\u4f20 ${uploadedFiles.length} \u4e2a\u6587\u4ef6`
        : "\u539f\u751f\u8868\u5355\u672a\u4e0a\u4f20\u4efb\u4f55\u9644\u4ef6"
    );
    setLocalFileStatus(
      uploadedFiles.length > 0 ? "is-submitted" : "is-empty",
      uploadedFiles.length > 0
        ? `\u5df2\u6210\u529f\u4e0a\u4f20 ${uploadedFiles.length} \u4e2a\u6587\u4ef6\uff0c\u73b0\u5728\u53ef\u4ee5\u751f\u6210 Prompt`
        : "\u539f\u751f\u8868\u5355\u63d0\u4ea4\u540e\u672a\u6536\u5230\u9644\u4ef6"
    );
    elements.fileUploadForm.reset();
    clearPendingSelection();
    state.lastObservedInputValue = "";
  });
}

async function bootstrap() {
  bindEvents();
  setSendButtonMode("send");
  resizePromptInput();
  refreshLayoutState();
  renderSelectedFiles();
  setBackendFileStatus("\u672a\u63d0\u4ea4");
  window.setInterval(() => {
    if (state.selectedFiles.length === 0) {
      syncFilesFromInput("\u5b9a\u65f6\u5de1\u68c0", true);
    }
  }, 1000);
  await Promise.all([loadWorkflow(false), loadAgents(false)]);
}

function setSendButtonMode(mode) {
  if (mode === "stop") {
    elements.sendBtn.textContent = "\u505c\u6b62";
    elements.sendBtn.title = "\u505c\u6b62\u5f53\u524d\u8bf7\u6c42";
    elements.sendBtn.classList.add("is-stop");
    return;
  }

  elements.sendBtn.textContent = "\u53d1\u9001";
  elements.sendBtn.title = "\u53d1\u9001";
  elements.sendBtn.classList.remove("is-stop");
}

function hasNewComposerFiles() {
  return (
    state.uploadInProgress ||
    state.pendingSelectionNames.length > 0 ||
    state.selectedFiles.some((file) => !file.uploaded || file.rawFile)
  );
}

function submitComposerInput() {
  const canReviseCurrentPrompt =
    Boolean(state.checkpointId) &&
    Boolean(state.latestPromptAction?.isConnected) &&
    !hasNewComposerFiles();

  if (canReviseCurrentPrompt) {
    revisePrompt(state.checkpointId);
    return;
  }

  generatePrompt();
}

async function waitForJob(jobId, { signal, onTick } = {}) {
  while (true) {
    const response = await fetch(`/session/jobs/${encodeURIComponent(jobId)}`, { signal });
    if (!response.ok) {
      throw new Error("job status failed");
    }
    const job = await response.json();
    onTick?.(job);
    if (job.status === "completed") {
      return job.result;
    }
    if (job.status === "canceled") {
      return job.result || { stage: "stopped" };
    }
    if (job.status === "failed") {
      const errorMessage = job.error?.message || job.error?.code || "job failed";
      throw new Error(errorMessage);
    }
    await new Promise((resolve, reject) => {
      const timeoutId = window.setTimeout(resolve, 900);
      signal?.addEventListener(
        "abort",
        () => {
          window.clearTimeout(timeoutId);
          reject(new DOMException("Aborted", "AbortError"));
        },
        { once: true }
      );
    });
  }
}

function beginRequest(kind, controller, pendingMessage) {
  state.activeRequestKind = kind;
  state.activeRequestController = controller;
  state.activePendingMessage = pendingMessage || null;
  state.activeRequestStartedAt = Date.now();
  stopActiveRuntimeTimer();
  updatePendingRuntimeLabel();
  if (state.activePendingMessage) {
    state.activeRequestTimerId = window.setInterval(updatePendingRuntimeLabel, 1000);
  }
  setSendButtonMode("stop");
}

function finishRequest() {
  stopActiveRuntimeTimer();
  const runtimeLabel = state.activeRequestStartedAt
    ? buildRuntimeLabel(state.activeRequestStartedAt)
    : "";
  state.activeRequestKind = null;
  state.activeRequestController = null;
  state.activeStopPayload = null;
  state.activePendingMessage = null;
  state.activeRequestStartedAt = null;
  setSendButtonMode("send");
  return runtimeLabel;
}

function stopCurrentRequest() {
  if (!state.activeRequestController) {
    return false;
  }

  if (state.activeStopPayload) {
    fetch("/session/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.activeStopPayload),
      keepalive: true,
    }).catch(() => {});
  }
  state.activeRequestController.abort();
  return true;
}

function appendGeneratingMessage(title = "\u6b63\u5728\u751f\u6210\u63d0\u793a\u8bcd") {
  return appendMessage(
    "agent",
    "drawAgent",
    [
      `<div class="assistant-status-head"><span class="assistant-card-title">${escapeHtml(title)}</span><span class="status-spinner" aria-hidden="true"></span></div>`,
      '<div class="assistant-runtime" data-role="assistant-runtime">已处理 0s</div>',
    ].join("")
  );
}

function appendImageGeneratingMessage() {
  return appendMessage(
    "agent",
    "drawAgent",
    [
      '<div class="assistant-status-head"><span class="assistant-card-title">\u6b63\u5728\u751f\u6210\u56fe\u7247</span><span class="status-spinner" aria-hidden="true"></span></div>',
      '<div class="assistant-runtime" data-role="assistant-runtime">已处理 0s</div>',
    ].join("")
  );
}

function clearLatestPromptAction() {
  if (!state.latestPromptAction) {
    return;
  }

  const messageBody = state.latestPromptAction.parentElement;
  if (messageBody) {
    messageBody.classList.remove("has-prompt-action");
  }

  state.latestPromptAction.remove();
  state.latestPromptAction = null;
}

function attachPromptAction(article, checkpointId) {
  const messageBody = article?.querySelector(".message-body");
  if (!messageBody || !checkpointId) {
    return;
  }

  clearLatestPromptAction();
  messageBody.classList.add("has-prompt-action");

  const actionWrap = document.createElement("div");
  actionWrap.className = "prompt-action-floating";
  actionWrap.setAttribute("role", "group");
  actionWrap.setAttribute("aria-label", "Prompt 后续操作");
  actionWrap.innerHTML =
    '<div class="prompt-action-copy">' +
    '<span class="prompt-action-eyebrow">下一步</span>' +
    '<strong class="prompt-action-title">下一步：确认后开始生图</strong>' +
    '<span class="prompt-action-hint">点击“开始生图”后，我将使用上面的提示词生成图片。如果需要调整，直接在输入框写下要求并发送，或点击“修改提示词”。</span>' +
    '</div>' +
    '<div class="prompt-action-buttons">' +
    '<button class="prompt-action-btn secondary" type="button" data-role="revise-prompt">修改提示词</button>' +
    '<button class="prompt-action-btn primary" type="button" data-role="confirm-image">\u5f00\u59cb\u751f\u56fe</button>' +
    '</div>';

  const button = actionWrap.querySelector('[data-role="confirm-image"]');
  button?.addEventListener("click", () => confirmImageGeneration(checkpointId, button));
  const reviseButton = actionWrap.querySelector('[data-role="revise-prompt"]');
  reviseButton?.addEventListener("click", () => revisePrompt(checkpointId, reviseButton));

  messageBody.appendChild(actionWrap);
  state.latestPromptAction = actionWrap;
  window.requestAnimationFrame(() => actionWrap.scrollIntoView({ behavior: "smooth", block: "center" }));
}

function attachOrchestrationAction(article, sessionId) {
  const messageBody = article?.querySelector(".message-body");
  if (!messageBody || !sessionId) {
    return;
  }

  clearLatestPromptAction();
  messageBody.classList.add("has-prompt-action");

  const actionWrap = document.createElement("div");
  actionWrap.className = "prompt-action-floating";
  actionWrap.setAttribute("role", "group");
  actionWrap.setAttribute("aria-label", "确认任务编排");
  actionWrap.setAttribute("aria-live", "polite");
  actionWrap.innerHTML =
    '<div class="prompt-action-copy">' +
    '<span class="prompt-action-eyebrow">需要你的确认</span>' +
    '<strong class="prompt-action-title">编排方案已准备好</strong>' +
    '<span class="prompt-action-hint">点击后我会按照上面的流程逐步处理，执行期间你可以随时停止。</span>' +
    '</div>' +
    '<div class="prompt-action-buttons">' +
    '<button class="prompt-action-btn primary" type="button" data-role="confirm-orchestration">确认编排，开始执行</button>' +
    '</div>';

  const button = actionWrap.querySelector('[data-role="confirm-orchestration"]');
  button?.addEventListener("click", () => confirmOrchestration(sessionId, button));

  messageBody.appendChild(actionWrap);
  state.latestPromptAction = actionWrap;
  window.requestAnimationFrame(() => actionWrap.scrollIntoView({ behavior: "smooth", block: "center" }));
}

async function generatePrompt(options = {}) {
  if (state.activeRequestController) {
    return;
  }

  const userInput = String(options.userInputOverride ?? elements.promptInput.value).trim();

  syncFilesFromInput("\u53d1\u9001\u524d\u68c0\u67e5", true);

  let uploadedFilesForRequest = state.selectedFiles.filter((file) => file.uploaded && file.saved_path);
  let pendingFiles = state.selectedFiles.filter((file) => !file.uploaded);
  let hasPendingSelection = pendingFiles.length > 0 || state.pendingSelectionNames.length > 0;

  clearLatestPromptAction();

  if (hasPendingSelection && !state.uploadInProgress) {
    try {
      const submitted = submitNativeUploadFallback("\u53d1\u9001\u524d\u539f\u751f\u8865\u4f20");
      if (!submitted) {
        if (pendingFiles.some((file) => file.rawFile)) {
          await uploadSelectedFiles();
        } else {
          await uploadFilesFromForm("\u53d1\u9001\u524d\u81ea\u52a8\u8865\u4f20");
        }
      }
    } catch (error) {
      // Keep the pending state for the validation message below.
    }

    uploadedFilesForRequest = state.selectedFiles.filter((file) => file.uploaded && file.saved_path);
    pendingFiles = state.selectedFiles.filter((file) => !file.uploaded);
    hasPendingSelection = pendingFiles.length > 0 || state.pendingSelectionNames.length > 0;
  }

  if (hasPendingSelection || state.uploadInProgress) {
    appendMessage(
      "agent",
      "drawAgent \u00b7 \u63d0\u793a",
      [
        '<span class="assistant-card-title">\u9644\u4ef6\u8fd8\u6ca1\u6709\u5b8c\u6210\u4e0a\u4f20</span>',
        "<div>\u7cfb\u7edf\u6b63\u5728\u8865\u4f20\u6216\u7b49\u5f85\u8bfb\u53d6\u9644\u4ef6\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002\u5982\u679c\u8fd9\u6761\u63d0\u793a\u91cd\u590d\u51fa\u73b0\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u4e00\u6b21\u9644\u4ef6\u3002</div>",
      ].join("")
    );
    return;
  }

  if (!userInput && uploadedFilesForRequest.length === 0) {
    appendMessage(
      "agent",
      "drawAgent \u00b7 \u63d0\u793a",
      [
        '<span class="assistant-card-title">\u8fd8\u6ca1\u6709\u8f93\u5165\u5185\u5bb9</span>',
        "<div>\u8bf7\u5148\u63cf\u8ff0\u4f60\u60f3\u751f\u6210\u7684\u8bba\u6587\u6846\u67b6\u56fe\uff0c\u6216\u8005\u5148\u5bfc\u5165\u6587\u4ef6\u3002</div>",
      ].join("")
    );
    return;
  }

  if (!options.skipUserMessage) {
    appendUserMessage(
      "\u7528\u6237",
      userInput || "\u8bf7\u57fa\u4e8e\u9644\u4ef6\u751f\u6210\u7ed8\u56fe Prompt",
      uploadedFilesForRequest
    );
  }
  clearPromptInput();

  resetComposerFiles();
  setThreadStatus("\u751f\u6210 Prompt \u4e2d");
  setBackendFileStatus(
    uploadedFilesForRequest.length > 0
      ? `\u6b63\u5728\u63d0\u4ea4 ${uploadedFilesForRequest.length} \u4e2a\u5df2\u4e0a\u4f20\u6587\u4ef6`
      : "\u672c\u6b21\u672a\u643a\u5e26\u9644\u4ef6"
  );
  elements.generatePromptBtn.disabled = true;
  elements.sendBtn.disabled = false;

  const requestSessionId = state.currentSessionId || createClientRequestId();
  state.currentSessionId = requestSessionId;
  const generatingMessage = appendGeneratingMessage();
  const controller = new AbortController();
  beginRequest("prompt", controller, generatingMessage);
  state.activeStopPayload = { session_id: requestSessionId };

  try {
    const formData = new FormData();
    formData.append("user_input", userInput);
    formData.append("session_id", requestSessionId);
    formData.append(
      "staged_files_json",
      JSON.stringify(
        uploadedFilesForRequest.map((file) => ({
          name: file.name,
          content_type: file.content_type || "application/octet-stream",
          size_bytes: file.size_bytes || 0,
          saved_path: file.saved_path,
        }))
      )
    );
    if (uploadedFilesForRequest.length > 0 && state.lastUploadBatchId) {
      formData.append("upload_batch_id", state.lastUploadBatchId);
    }

    const response = await fetch("/session/start-job", {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });

    const job = await response.json();
    const data = await waitForJob(job.job_id, {
      signal: controller.signal,
      onTick: (latestJob) => {
        if (latestJob.stage && latestJob.stage !== "running") {
          setThreadStatus(`处理中：${latestJob.stage}`);
        }
      },
    });
    state.checkpointId = data.checkpoint_id;
    state.currentSessionId = data.session_id || requestSessionId;
    setCheckpoint(data.checkpoint_id ? "\u5df2\u751f\u6210" : "\u672a\u751f\u6210");
    const reviewApproved = data.payload_final?.value?.review_approved !== false;
    setThreadStatus(
      data.checkpoint_id
        ? reviewApproved
          ? "\u7b49\u5f85\u786e\u8ba4"
          : "\u5f85\u786e\u8ba4\uff08\u672a\u901a\u8fc7\u5ba1\u67e5\uff09"
        : "\u5f85\u4fee\u8ba2"
    );
    setBackendFileStatus(
      data.uploaded_files?.length > 0
        ? `\u540e\u7aef\u5df2\u6536\u5230 ${data.uploaded_files.length} \u4e2a\u5b9e\u9645\u6587\u4ef6`
        : "\u672c\u6b21\u672a\u63d0\u4ea4\u9644\u4ef6"
    );

    if (data.uploaded_files?.length > 0) {
      setLocalFileStatus(
        "is-submitted",
        `\u5df2\u5b8c\u6210\u4e0a\u4f20\uff1a\u540e\u7aef\u5df2\u4fdd\u5b58 ${data.uploaded_files.length} \u4e2a\u5b9e\u9645\u6587\u4ef6`
      );
    }

    generatingMessage?.remove();
    const runtimeLabel = finishRequest();
    const promptMessage = appendMessage("agent", "drawAgent", formatPromptBubble(data), { runtimeLabel });
    if (data.checkpoint_id) {
      attachPromptAction(promptMessage, data.checkpoint_id);
    } else if (data.stage === "awaiting_orchestration_confirmation") {
      attachOrchestrationAction(promptMessage, data.session_id || state.currentSessionId);
    }
    clearPromptInput();
  } catch (error) {
    generatingMessage?.remove();
    const runtimeLabel = finishRequest();

    if (error?.name === "AbortError") {
      setThreadStatus("\u5df2\u505c\u6b62");
      setBackendFileStatus("\u5df2\u505c\u6b62\u672c\u6b21\u8bf7\u6c42");
      appendMessage(
        "agent",
        "drawAgent",
        '<span class="assistant-card-title">\u5df2\u505c\u6b62\u672c\u6b21\u751f\u6210</span>',
        { runtimeLabel }
      );
    } else {
      setThreadStatus("\u8bf7\u6c42\u5931\u8d25");
      setBackendFileStatus("\u63d0\u4ea4\u5931\u8d25");
      appendMessage(
        "agent",
        "drawAgent \u00b7 \u9519\u8bef",
        [
          '<span class="assistant-card-title">\u751f\u6210 Prompt \u5931\u8d25</span>',
          "<div>\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002</div>",
        ].join(""),
        { runtimeLabel }
      );
    }
  } finally {
    elements.generatePromptBtn.disabled = false;
    elements.sendBtn.disabled = false;
  }
}

async function revisePrompt(checkpointId = state.checkpointId, actionButton = null) {
  if (!checkpointId || state.activeRequestController) {
    return;
  }
  const revisionInstruction = elements.promptInput.value.trim();
  if (!revisionInstruction) {
    appendMessage(
      "agent",
      "drawAgent \u00b7 \u63d0\u793a",
      '<span class="assistant-card-title">请先写下修改要求</span><div>在输入框里告诉我想调整什么，然后点击“修改提示词”，我会重新生成并再次检查。</div>'
    );
    return;
  }
  if (actionButton) {
    actionButton.disabled = true;
    actionButton.textContent = "正在重新生成…";
  }
  appendUserMessage("用户", revisionInstruction);
  clearPromptInput();
  clearLatestPromptAction();
  setThreadStatus("正在判断修改范围");
  const pendingMessage = appendGeneratingMessage("正在按你的要求修改提示词");
  const controller = new AbortController();
  beginRequest("prompt_revision", controller, pendingMessage);
  state.activeStopPayload = { checkpoint_id: checkpointId };
  let requestFinished = false;
  try {
    const response = await fetch("/session/revise-prompt-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        checkpoint_id: checkpointId,
        revision_instruction: revisionInstruction,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error("revision failed");
    }
    const job = await response.json();
    const data = await waitForJob(job.job_id, {
      signal: controller.signal,
      onTick: (latestJob) => {
        if (latestJob.stage && latestJob.stage !== "running") {
          setThreadStatus(`修改中：${latestJob.stage}`);
        }
      },
    });

    pendingMessage?.remove();
    const runtimeLabel = finishRequest();
    requestFinished = true;

    if (data.route_action === "full_pipeline") {
      setThreadStatus("需要重新分析相关内容");
      await generatePrompt({
        userInputOverride: revisionInstruction,
        skipUserMessage: true,
      });
      return;
    }

    if (data.route_action === "clarification_needed") {
      setThreadStatus("等待补充说明");
      const clarificationMessage = appendMessage(
        "agent",
        "drawAgent",
        `<span class="assistant-card-title">我需要确认一下</span><div>${escapeHtml(
          data.question || "你希望修改当前图的哪一部分？"
        )}</div>`,
        { runtimeLabel }
      );
      attachPromptAction(clarificationMessage, checkpointId);
      return;
    }

    state.checkpointId = data.checkpoint_id;
    state.currentSessionId = data.session_id || state.currentSessionId;
    setCheckpoint(data.checkpoint_id ? "\u5df2\u751f\u6210" : "\u672a\u751f\u6210");
    const reviewApproved = data.workflow_state?.final_response?.review_approved !== false;
    setThreadStatus(reviewApproved ? "修改完成，等待确认" : "修改完成，请检查");
    const promptMessage = appendMessage("agent", "drawAgent", formatPromptBubble(data), { runtimeLabel });
    attachPromptAction(promptMessage, data.checkpoint_id);
  } catch (error) {
    pendingMessage?.remove();
    const runtimeLabel = requestFinished ? "" : finishRequest();
    requestFinished = true;
    if (error?.name === "AbortError") {
      setThreadStatus("已停止");
      appendMessage(
        "agent",
        "drawAgent",
        '<span class="assistant-card-title">已停止本次提示词修改</span>',
        { runtimeLabel }
      );
    } else {
      setThreadStatus("修改失败");
      appendMessage(
        "agent",
        "drawAgent \u00b7 \u9519\u8bef",
        '<span class="assistant-card-title">修改提示词失败</span><div>原提示词没有被覆盖，你可以稍后重试。</div>',
        { runtimeLabel }
      );
    }
  } finally {
    if (!requestFinished && state.activeRequestController === controller) {
      finishRequest();
    }
    if (actionButton) {
      actionButton.disabled = false;
      actionButton.textContent = "修改提示词";
    }
  }
}

async function confirmOrchestration(sessionId = state.currentSessionId, actionButton = null) {
  if (!sessionId || state.activeRequestController) {
    return;
  }

  const pendingMessage = appendGeneratingMessage("\u6b63\u5728\u6267\u884c\u4efb\u52a1\u7f16\u6392");
  const controller = new AbortController();
  beginRequest("orchestration", controller, pendingMessage);
  state.activeStopPayload = { session_id: sessionId };
  setThreadStatus("执行编排中");

  if (actionButton) {
    actionButton.disabled = true;
    actionButton.classList.add("is-loading");
    actionButton.textContent = "正在执行编排…";
  }

  try {
    const response = await fetch("/session/confirm-orchestration-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error("confirm orchestration failed");
    }

    const job = await response.json();
    const data = await waitForJob(job.job_id, {
      signal: controller.signal,
      onTick: (latestJob) => {
        if (latestJob.stage && latestJob.stage !== "running") {
          setThreadStatus(`处理中：${latestJob.stage}`);
        }
      },
    });
    state.checkpointId = data.checkpoint_id;
    state.currentSessionId = data.session_id || sessionId;
    setCheckpoint(data.checkpoint_id ? "已生成" : "未生成");
    setThreadStatus(data.checkpoint_id ? "等待确认" : "待修订");
    clearLatestPromptAction();
    pendingMessage?.remove();
    const runtimeLabel = finishRequest();
    const promptMessage = appendMessage("agent", "drawAgent", formatPromptBubble(data), { runtimeLabel });
    if (data.checkpoint_id) {
      attachPromptAction(promptMessage, data.checkpoint_id);
    }
  } catch (error) {
    pendingMessage?.remove();
    const runtimeLabel = finishRequest();
    if (error?.name === "AbortError") {
      setThreadStatus("已停止");
      appendMessage(
        "agent",
        "drawAgent",
        '<span class="assistant-card-title">已停止本次执行</span>',
        { runtimeLabel }
      );
    } else {
      setThreadStatus("执行失败");
      appendMessage(
        "agent",
        "drawAgent · 错误",
        '<span class="assistant-card-title">确认编排失败</span><div>请稍后重试。</div>',
        { runtimeLabel }
      );
    }
  } finally {
    if (actionButton) {
      actionButton.disabled = false;
      actionButton.classList.remove("is-loading");
      actionButton.textContent = "确认编排，开始执行";
    }
  }
}

async function confirmImageGeneration(checkpointId = state.checkpointId, actionButton = null) {
  if (!checkpointId || state.activeRequestController) {
    return;
  }

  const pendingImageMessage = appendImageGeneratingMessage();
  const controller = new AbortController();
  beginRequest("image", controller, pendingImageMessage);
  state.activeStopPayload = { checkpoint_id: checkpointId };
  setThreadStatus("\u63d0\u4ea4\u51fa\u56fe\u8bf7\u6c42\u4e2d");

  if (actionButton) {
    actionButton.disabled = true;
    actionButton.classList.add("is-loading");
    actionButton.textContent = "\u6b63\u5728\u751f\u6210\u56fe\u7247\u2026";
  }

  try {
    const response = await fetch("/session/confirm-image-generation-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checkpoint_id: checkpointId }),
      signal: controller.signal,
    });

    const job = await response.json();
    const data = await waitForJob(job.job_id, {
      signal: controller.signal,
      onTick: (latestJob) => {
        if (latestJob.stage && latestJob.stage !== "running") {
          setThreadStatus(`出图中：${latestJob.stage}`);
        }
      },
    });
    if (data.stage === "image_generation_completed") {
      setThreadStatus("\u56fe\u7247\u5df2\u751f\u6210");
      clearLatestPromptAction();
    } else if (data.stage === "image_generation_failed") {
      setThreadStatus("\u51fa\u56fe\u5931\u8d25");
    } else if (data.stage === "image_generation_not_implemented") {
      setThreadStatus("\u51fa\u56fe\u94fe\u8def\u672a\u63a5\u901a");
    } else if (data.stage === "stopped") {
      setThreadStatus("\u5df2\u505c\u6b62");
    } else {
      setThreadStatus("\u51fa\u56fe\u4e2d");
    }

    if (
      (data.stage === "image_generation_failed" || data.stage === "image_generation_not_implemented") &&
      actionButton
    ) {
      actionButton.disabled = false;
      actionButton.classList.remove("is-loading");
      actionButton.textContent = "\u5f00\u59cb\u751f\u56fe";
    }

    pendingImageMessage?.remove();
    const runtimeLabel = finishRequest();
    appendMessage("agent", "drawAgent", formatImageStageBubble(data), {
      runtimeLabel,
    });
  } catch (error) {
    pendingImageMessage?.remove();
    const runtimeLabel = finishRequest();
    if (error?.name === "AbortError") {
      setThreadStatus("\u5df2\u505c\u6b62");
      if (actionButton) {
        actionButton.disabled = false;
        actionButton.classList.remove("is-loading");
        actionButton.textContent = "\u5f00\u59cb\u751f\u56fe";
      }
      appendMessage(
        "agent",
        "drawAgent",
        '<span class="assistant-card-title">\u5df2\u505c\u6b62\u672c\u6b21\u751f\u56fe</span>',
        { runtimeLabel }
      );
    } else {
      setThreadStatus("\u786e\u8ba4\u5931\u8d25");
      if (actionButton) {
        actionButton.disabled = false;
        actionButton.classList.remove("is-loading");
        actionButton.textContent = "\u5f00\u59cb\u751f\u56fe";
      }
      appendMessage(
        "agent",
        "drawAgent \u00b7 \u9519\u8bef",
        [
          '<span class="assistant-card-title">\u786e\u8ba4\u51fa\u56fe\u5931\u8d25</span>',
          "<div>\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002</div>",
        ].join(""),
        { runtimeLabel }
      );
    }
  } finally {
  }
}

bootstrap();
