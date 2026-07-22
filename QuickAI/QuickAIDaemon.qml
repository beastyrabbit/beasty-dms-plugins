import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Modals.Common
import qs.Modules.Plugins
import qs.Services
import qs.Widgets

PluginComponent {
    id: root

    property string mode: "ask"
    property string inputText: ""
    property string targetLanguage: "auto"
    property string status: "idle"
    property string answer: ""
    property var translation: ({
    })
    property string errorMessage: ""
    property int elapsedMs: 0
    property int elapsedSeconds: 0
    property int generation: 0
    property var pendingRequest: null
    property var activeProcess: null
    property bool autoRunOnOpen: false
    property bool clipboardSource: false
    property double lastNonce: -1
    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("quick_ai.py"))
    readonly property int queryLimit: mode === "ask" ? 4000 : (mode === "translate" ? 1200 : 24000)

    function displayTitle() {
        if (mode === "translate")
            return "Quick Translate";

        if (mode === "summarize")
            return "Quick Summary";

        return "Quick Ask";
    }

    function displayIcon() {
        if (mode === "translate")
            return "translate";

        if (mode === "summarize")
            return "summarize";

        return "bolt";
    }

    function displaySubtitle() {
        if (mode === "translate")
            return "Terra · Low · Standard · English ↔ German";

        if (mode === "summarize")
            return "Terra · Low · Standard · " + (clipboardSource ? "Clipboard" : "Input");

        return "Terra · Low · Standard · Web when needed";
    }

    function loadingText() {
        if (mode === "translate")
            return "Translating… " + elapsedSeconds + "s";

        if (mode === "summarize")
            return "Summarizing… " + elapsedSeconds + "s";

        return "Thinking… " + elapsedSeconds + "s";
    }

    function openRequest(request) {
        if (!request || request.nonce === lastNonce)
            return ;

        const text = (request.text || "").trim();
        const requestedMode = request.mode === "translate" || request.mode === "summarize" ? request.mode : "ask";
        const requestedClipboard = requestedMode === "summarize" && request.clipboard === true;
        if (!text && !requestedClipboard)
            return ;

        lastNonce = request.nonce;
        cancelRequest();
        mode = requestedMode;
        clipboardSource = requestedClipboard;
        targetLanguage = "auto";
        inputText = requestedClipboard ? "" : text;
        answer = "";
        translation = ({
        });
        errorMessage = "";
        elapsedMs = 0;
        status = "idle";
        autoRunOnOpen = true;
        quickModal.open();
    }

    function submit() {
        const text = inputText.trim();
        if ((!text && !clipboardSource) || text.length > queryLimit)
            return ;

        generation += 1;
        pendingRequest = {
            "generation": generation,
            "mode": mode,
            "target": targetLanguage,
            "query": text,
            "clipboard": clipboardSource
        };
        answer = "";
        translation = ({
        });
        errorMessage = "";
        elapsedMs = 0;
        elapsedSeconds = 0;
        status = "loading";
        if (activeProcess && activeProcess.running) {
            activeProcess.running = false;
            return ;
        }
        Qt.callLater(startPendingRequest);
    }

    function startPendingRequest() {
        if (!pendingRequest || activeProcess)
            return ;

        const request = pendingRequest;
        pendingRequest = null;
        const arguments = ["python3", helperPath, "--mode", request.mode, "--target", request.target];
        if (request.clipboard)
            arguments.push("--clipboard");
        else
            arguments.push("--query", request.query);
        activeProcess = requestProcess.createObject(root, {
            "requestGeneration": request.generation,
            "command": arguments
        });
        activeProcess.running = true;
    }

    function processFinished(requestGeneration, exitCode, stdoutText) {
        activeProcess = null;
        if (pendingRequest) {
            Qt.callLater(startPendingRequest);
            return ;
        }
        if (requestGeneration !== generation)
            return ;

        let payload = null;
        try {
            payload = JSON.parse((stdoutText || "").trim());
        } catch (error) {
            payload = null;
        }
        if (exitCode !== 0 || !payload) {
            status = "error";
            errorMessage = "Quick AI stopped unexpectedly. Try again.";
            return ;
        }
        elapsedMs = payload.elapsedMs || 0;
        if (!payload.ok) {
            status = payload.code === "cancelled" ? "idle" : "error";
            errorMessage = payload.message || "Codex could not complete the request.";
            return ;
        }
        if (payload.mode === "translate")
            translation = payload.result || ({
        });
        else
            answer = payload.answer || "";
        status = "ready";
    }

    function cancelRequest() {
        generation += 1;
        pendingRequest = null;
        if (activeProcess && activeProcess.running)
            activeProcess.running = false;

    }

    function copyResult() {
        const text = mode === "translate" ? (translation.translation || "") : answer;
        if (!text)
            return ;

        Quickshell.execDetached(["dms", "cl", "copy", text]);
        ToastService.showInfo(displayTitle(), "Copied to clipboard");
    }

    function resetAfterClose() {
        cancelRequest();
        status = "idle";
        inputText = "";
        answer = "";
        translation = ({
        });
        errorMessage = "";
        elapsedMs = 0;
        elapsedSeconds = 0;
        clipboardSource = false;
    }

    Component.onCompleted: {
        const existing = pluginService ? pluginService.getGlobalVar("quickAi", "openRequest", null) : null;
        if (existing)
            openRequest(existing);

    }
    Component.onDestruction: cancelRequest()

    Connections {
        function onGlobalVarChanged(pluginId, varName) {
            if (pluginId !== "quickAi" || varName !== "openRequest")
                return ;

            const request = root.pluginService.getGlobalVar("quickAi", "openRequest", null);
            Qt.callLater(() => {
                return root.openRequest(request);
            });
        }

        target: root.pluginService
    }

    Timer {
        interval: 1000
        repeat: true
        running: root.status === "loading"
        onTriggered: root.elapsedSeconds += 1
    }

    Component {
        id: requestProcess

        Process {
            id: process

            property int requestGeneration: 0

            onExited: (exitCode) => {
                root.processFinished(requestGeneration, exitCode, stdoutCollector.text);
                destroy();
            }

            stdout: StdioCollector {
                id: stdoutCollector
            }

            stderr: StdioCollector {
            }

        }

    }

    DankModal {
        id: quickModal

        layerNamespace: "dms:quick-ai"
        modalWidth: Math.min(660, screenWidth - Theme.spacingL * 2)
        modalHeight: Math.min(root.mode === "translate" ? 460 : 400, screenHeight - Theme.spacingL * 2)
        closeOnEscapeKey: true
        closeOnBackgroundClick: true
        keepContentLoaded: true
        onOpened: Qt.callLater(() => {
            const item = contentLoader ? contentLoader.item : null;
            if (item)
                item.focusOutput();

            if (root.autoRunOnOpen) {
                root.autoRunOnOpen = false;
                root.submit();
            }
        })
        onDialogClosed: root.resetAfterClose()

        content: Component {
            FocusScope {
                id: modalContent

                function focusOutput() {
                    modalContent.forceActiveFocus();
                }

                Keys.onPressed: (event) => {
                    if (event.key === Qt.Key_Escape) {
                        quickModal.close();
                        event.accepted = true;
                    }
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingL
                    spacing: Theme.spacingM

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingM

                        Rectangle {
                            implicitWidth: 42
                            implicitHeight: 42
                            radius: 14
                            color: Theme.primaryContainer

                            DankIcon {
                                anchors.centerIn: parent
                                name: root.displayIcon()
                                size: 22
                                color: Theme.primary
                            }

                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1

                            StyledText {
                                Layout.fillWidth: true
                                text: root.displayTitle()
                                font.pixelSize: Theme.fontSizeLarge
                                font.weight: Font.DemiBold
                                color: Theme.surfaceText
                            }

                            StyledText {
                                Layout.fillWidth: true
                                text: root.displaySubtitle()
                                font.pixelSize: Theme.fontSizeSmall
                                color: Theme.surfaceTextSecondary
                            }

                        }

                        DankButton {
                            text: "Close"
                            iconName: "close"
                            buttonHeight: 34
                            backgroundColor: Theme.surfaceContainerHigh
                            textColor: Theme.surfaceText
                            onClicked: quickModal.close()
                        }

                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: Theme.cornerRadius
                        color: Theme.surfaceContainerLow
                        border.width: 1
                        border.color: Theme.outlineLight

                        Item {
                            anchors.fill: parent
                            anchors.margins: Theme.spacingM

                            Column {
                                anchors.centerIn: parent
                                spacing: Theme.spacingM
                                visible: root.status === "idle"

                                DankIcon {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    name: root.displayIcon()
                                    size: 30
                                    color: Theme.surfaceTextSecondary
                                }

                                StyledText {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: root.mode === "translate" ? "Type text after ? and select Quick Translate" : (root.mode === "summarize" ? "Select a summary option from the ? menu" : "Type your question after ? in the launcher")
                                    color: Theme.surfaceTextSecondary
                                }

                            }

                            Column {
                                anchors.centerIn: parent
                                spacing: Theme.spacingM
                                visible: root.status === "loading"

                                DankSpinner {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    size: 34
                                    running: visible
                                }

                                StyledText {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: root.loadingText()
                                    color: Theme.surfaceTextMedium
                                }

                            }

                            Column {
                                anchors.centerIn: parent
                                width: Math.min(parent.width, 440)
                                spacing: Theme.spacingM
                                visible: root.status === "error"

                                DankIcon {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    name: "error_outline"
                                    size: 30
                                    color: Theme.error
                                }

                                StyledText {
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    text: root.errorMessage
                                    color: Theme.surfaceText
                                }

                            }

                            DankFlickable {
                                id: askScroll

                                anchors.fill: parent
                                visible: root.status === "ready" && root.mode !== "translate"
                                clip: true
                                contentWidth: width
                                contentHeight: Math.max(height, answerText.implicitHeight)

                                TextEdit {
                                    id: answerText

                                    width: askScroll.width
                                    text: root.answer
                                    textFormat: TextEdit.MarkdownText
                                    wrapMode: TextEdit.Wrap
                                    readOnly: true
                                    selectByMouse: true
                                    color: Theme.surfaceText
                                    selectionColor: Theme.primarySelected
                                    selectedTextColor: Theme.surfaceText
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSizeMedium
                                    onLinkActivated: (link) => {
                                        return Qt.openUrlExternally(link);
                                    }
                                }

                            }

                            DankFlickable {
                                id: translationScroll

                                anchors.fill: parent
                                visible: root.status === "ready" && root.mode === "translate"
                                clip: true
                                contentWidth: width
                                contentHeight: translationContent.implicitHeight

                                ColumnLayout {
                                    id: translationContent

                                    width: translationScroll.width
                                    spacing: Theme.spacingM

                                    RowLayout {
                                        Layout.fillWidth: true

                                        StyledText {
                                            Layout.fillWidth: true
                                            text: root.translation.translation || ""
                                            color: Theme.primary
                                            font.pixelSize: Theme.fontSizeXLarge
                                            font.weight: Font.DemiBold
                                            wrapMode: Text.Wrap
                                        }

                                        DankButton {
                                            Layout.alignment: Qt.AlignTop
                                            text: "Copy"
                                            iconName: "content_copy"
                                            buttonHeight: 34
                                            backgroundColor: Theme.primaryContainer
                                            textColor: Theme.primary
                                            onClicked: root.copyResult()
                                        }

                                    }

                                    StyledText {
                                        Layout.fillWidth: true
                                        text: (root.translation.sourceLanguage || "?").toUpperCase() + " → " + (root.translation.targetLanguage || "?").toUpperCase()
                                        color: Theme.surfaceTextSecondary
                                        font.pixelSize: Theme.fontSizeSmall
                                    }

                                    Rectangle {
                                        Layout.fillWidth: true
                                        visible: alternativesRepeater.count > 0
                                        implicitHeight: alternativesColumn.implicitHeight + Theme.spacingM * 2
                                        radius: Theme.cornerRadius
                                        color: Theme.surfaceContainerHigh

                                        ColumnLayout {
                                            id: alternativesColumn

                                            anchors.fill: parent
                                            anchors.margins: Theme.spacingM
                                            spacing: Theme.spacingS

                                            StyledText {
                                                text: "Alternatives"
                                                color: Theme.surfaceTextSecondary
                                                font.pixelSize: Theme.fontSizeSmall
                                                font.weight: Font.DemiBold
                                            }

                                            Repeater {
                                                id: alternativesRepeater

                                                model: root.translation.alternatives || []

                                                StyledText {
                                                    required property var modelData

                                                    Layout.fillWidth: true
                                                    text: "• " + modelData.translation + (modelData.nuance ? " — " + modelData.nuance : "")
                                                    color: Theme.surfaceText
                                                    wrapMode: Text.Wrap
                                                }

                                            }

                                        }

                                    }

                                    InfoRow {
                                        Layout.fillWidth: true
                                        visible: (root.translation.grammar || "").length > 0
                                        label: "Grammar"
                                        value: root.translation.grammar || ""
                                    }

                                    InfoRow {
                                        Layout.fillWidth: true
                                        visible: (root.translation.usage || "").length > 0
                                        label: "Usage"
                                        value: root.translation.usage || ""
                                    }

                                    InfoRow {
                                        Layout.fillWidth: true
                                        visible: (root.translation.ambiguityNote || "").length > 0
                                        label: root.translation.ambiguous ? "Ambiguity" : "Note"
                                        value: root.translation.ambiguityNote || ""
                                        accent: true
                                    }

                                }

                            }

                        }

                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingS

                        StyledText {
                            visible: root.status === "ready" && root.elapsedMs > 0
                            text: (root.elapsedMs / 1000).toFixed(1) + "s"
                            color: Theme.surfaceTextSecondary
                            font.pixelSize: Theme.fontSizeSmall
                        }

                        Item {
                            Layout.fillWidth: true
                        }

                        DankButton {
                            visible: root.status === "loading"
                            text: "Stop"
                            iconName: "stop"
                            buttonHeight: 36
                            backgroundColor: Theme.surfaceContainerHigh
                            textColor: Theme.surfaceText
                            onClicked: quickModal.close()
                        }

                        DankButton {
                            visible: root.status === "ready" && root.mode !== "translate"
                            text: "Copy"
                            iconName: "content_copy"
                            buttonHeight: 36
                            backgroundColor: Theme.surfaceContainerHigh
                            textColor: Theme.surfaceText
                            onClicked: root.copyResult()
                        }

                    }

                }

            }

        }

    }

    component InfoRow: Rectangle {
        id: infoRow

        property string label: ""
        property string value: ""
        property bool accent: false

        implicitHeight: infoLayout.implicitHeight + Theme.spacingM * 2
        radius: Theme.cornerRadius
        color: accent ? Theme.withAlpha(Theme.primary, 0.1) : Theme.surfaceContainerHigh

        ColumnLayout {
            id: infoLayout

            anchors.fill: parent
            anchors.margins: Theme.spacingM
            spacing: Theme.spacingXS

            StyledText {
                text: infoRow.label
                color: infoRow.accent ? Theme.primary : Theme.surfaceTextSecondary
                font.pixelSize: Theme.fontSizeSmall
                font.weight: Font.DemiBold
            }

            StyledText {
                Layout.fillWidth: true
                text: infoRow.value
                color: Theme.surfaceText
                wrapMode: Text.Wrap
            }

        }

    }

}
