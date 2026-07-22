import QtQuick

Item {
    id: root

    property var pluginService: null
    property string trigger: "?"

    signal itemsChanged()

    function getItems(query) {
        const text = (query || "").trim();
        const preview = text.length > 72 ? text.slice(0, 69) + "…" : text;
        return [{
            "name": text ? "Ask: " + preview : "Quick Ask",
            "icon": "material:bolt",
            "comment": text ? "Ask Terra now · low reasoning · web when needed" : "Type your question after ?",
            "action": "open",
            "mode": "ask",
            "requestText": text,
            "categories": ["Quick AI"]
        }, {
            "name": text ? "Translate: " + preview : "Quick Translate",
            "icon": "material:translate",
            "comment": text ? "Translate English ↔ German with Terra" : "Type text after ?, then press ↓",
            "action": "open",
            "mode": "translate",
            "requestText": text,
            "categories": ["Quick AI"]
        }, {
            "name": text ? "Summarize: " + preview : "Summarize Input",
            "icon": "material:summarize",
            "comment": text ? "Summarize the input with Terra" : "Type text after ?, then select this option",
            "action": "open",
            "mode": "summarize",
            "requestText": text,
            "clipboard": false,
            "categories": ["Quick AI"]
        }, {
            "name": "Summarize Clipboard",
            "icon": "material:content_paste",
            "comment": "Send the current plain-text clipboard to Terra for summarization",
            "action": "open",
            "mode": "summarize",
            "requestText": "",
            "clipboard": true,
            "categories": ["Quick AI"]
        }];
    }

    function executeItem(item) {
        const text = item && item.requestText ? item.requestText.trim() : "";
        const fromClipboard = item && item.clipboard === true;
        if (!pluginService || (!text && !fromClipboard))
            return ;

        const requestedMode = item && (item.mode === "translate" || item.mode === "summarize") ? item.mode : "ask";
        pluginService.setGlobalVar("quickAi", "openRequest", {
            "mode": requestedMode,
            "text": text,
            "clipboard": fromClipboard,
            "nonce": Date.now()
        });
    }

}
