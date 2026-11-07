/** vscode stub for §R6 auth-bridge tests — surfaces the commands the
 * extension-host would execute so a unit test can observe dispatch. */
module.exports = {
  _commandLog: [],

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

  commands: {
    executeCommand: async (name, ...args) => {
      module.exports._commandLog.push({ name, args });
    },
    registerCommand: () => ({ dispose: () => {} }),
  },

  window: {
    showInformationMessage: async () => undefined,
    showErrorMessage: async () => undefined,
    showInputBox: async () => undefined,
  },

  workspace: {
    getConfiguration() {
      return { get: () => undefined };
    },
    workspaceFolders: [],
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
};
