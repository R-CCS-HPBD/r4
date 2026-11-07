/** Minimal vscode stub for unit tests that bundle provider helpers. */
module.exports = {
  EventEmitter: class {
    constructor() {
      this._listeners = [];
    }
    event(listener) {
      this._listeners.push(listener);
      return { dispose: () => {} };
    }
    fire(data) {
      for (const l of this._listeners) l(data);
    }
    dispose() {}
  },
  workspace: {
    getConfiguration() {
      // Like VS Code: an unset key returns the caller's default.
      return { get: (_key, fallback) => fallback };
    },
    /** D5: editor-context chip uses this; tests model "no folder". */
    asRelativePath(uri) {
      return typeof uri === "string" ? uri : uri?.fsPath ?? "";
    },
  },
  LanguageModelChatMessageRole: { User: 1, Assistant: 2 },
  LanguageModelTextPart: class {
    constructor(value) {
      this.value = value;
    }
  },
  LanguageModelToolCallPart: class {
    constructor(callId, name, input) {
      this.callId = callId;
      this.name = name;
      this.input = input;
    }
  },
  LanguageModelToolResultPart: class {
    constructor(callId, content) {
      this.callId = callId;
      this.content = content;
    }
  },
  LanguageModelDataPart: class {
    constructor(data, mimeType) {
      this.data = data;
      this.mimeType = mimeType;
    }
  },
  LanguageModelThinkingPart: class {
    constructor(value) {
      this.value = value;
    }
  },
  window: {
    showWarningMessage: async (_message, _options, ...items) => items[0],
    /** D5: no active editor in unit tests → chip === null. */
    activeTextEditor: undefined,
    onDidChangeTextEditorSelection() {
      return { dispose: () => {} };
    },
    onDidChangeActiveTextEditor() {
      return { dispose: () => {} };
    },
  },
};
