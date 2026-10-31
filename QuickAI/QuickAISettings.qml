import QtQuick
import qs.Common
import qs.DCommon.Widgets
import qs.Modules.Plugins

PluginSettings {
    id: root

    readonly property string defaultModel: "deepseek/deepseek-v4.1-flash"
    // Keep in sync with FAST_MODELS in quick_ai.py.
    readonly property var models: ["deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash-1017", "xiaomi/mimo-v2.6-flash", "z-ai/glm-5.3-flash", "google/gemini-3.5-flash-lite", "anthropic/claude-haiku-5.5"]

    // A saved model that left the list stays selectable.
    readonly property var modelOptions: {
        const ids = models.slice();
        const saved = pluginService ? loadValue("fastModel", "") : "";
        if (saved && ids.indexOf(saved) === -1)
            ids.push(saved);

        return ids.map(id => ({
                    "label": id === defaultModel ? id + " (default)" : id,
                    "value": id
                }));
    }

    pluginId: "quickAi"

    StyledText {
        width: parent.width
        text: "Quick AI"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Theme.fontWeightMedium
        color: Theme.surfaceText
    }

    SelectionSetting {
        settingKey: "fastModel"
        label: "Model"
        description: "OpenRouter model for Quick Ask, Translate, and Summarize. Needs OPENROUTER_API_KEY in the DMS environment."
        options: root.modelOptions
        defaultValue: root.defaultModel
    }

    SelectionSetting {
        settingKey: "thinking"
        label: "Thinking level"
        description: "How long the model reasons before answering. Higher levels are slower. GLM and Gemini Flash-Lite cannot turn thinking off."
        defaultValue: "off"
        options: [{
            "label": "Off (default)",
            "value": "off"
        }, {
            "label": "Low",
            "value": "low"
        }, {
            "label": "Medium",
            "value": "medium"
        }, {
            "label": "High",
            "value": "high"
        }]
    }

    StyledText {
        width: parent.width
        text: "Better answer reruns the request on GPT-6.1 Sol with medium thinking through the CLIProxyAPI provider in ~/.codex/config.toml."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceTextSecondary
        wrapMode: Text.WordWrap
    }

}
