import QtQuick
import qs.Common
import qs.DCommon.Widgets
import qs.Modules.Plugins

PluginSettings {
    id: root

    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("quick_ai.py"))
    property string defaultModel: "gpt-5.6-terra"
    property var models: []
    property string loadError: ""

    // The saved model stays selectable even if the proxy is unreachable.
    readonly property var modelOptions: {
        const ids = models.slice();
        const saved = pluginService ? loadValue("model", "") : "";
        for (const id of [defaultModel, saved]) {
            if (id && ids.indexOf(id) === -1)
                ids.push(id);
        }
        return ids.sort().map(id => ({
                    "label": id === defaultModel ? id + " (default)" : id,
                    "value": id
                }));
    }

    pluginId: "quickAi"

    Component.onCompleted: {
        Proc.runCommand("quickAi.listModels", ["python3", helperPath, "--list-models"], (output, exitCode) => {
            let result = null;
            try {
                result = JSON.parse(output.trim());
            } catch (error) {
                result = null;
            }
            if (exitCode !== 0 || !result) {
                root.loadError = "Could not list models.";
                return ;
            }
            root.defaultModel = result.default || root.defaultModel;
            if (!result.ok) {
                root.loadError = result.message || "Could not list models.";
                return ;
            }
            root.models = result.models || [];
        });
    }

    StyledText {
        width: parent.width
        text: "Quick AI"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Theme.fontWeightMedium
        color: Theme.surfaceText
    }

    SelectionSetting {
        settingKey: "model"
        label: "Model"
        description: root.loadError || "Used for Quick Ask, Translate, and Summarize. Models come from the Codex model provider (CLIProxyAPI)."
        options: root.modelOptions
        defaultValue: root.defaultModel
    }

}
