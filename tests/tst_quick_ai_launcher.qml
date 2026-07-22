import "../QuickAI" as QuickAI
import QtQuick
import QtTest

TestCase {
    function test_actions_keep_ask_default_and_translate_second() {
        const items = launcher.getItems("apfelbaum");
        compare(items.length, 4);
        compare(items[0].mode, "ask");
        compare(items[0].requestText, "apfelbaum");
        compare(items[1].mode, "translate");
        compare(items[1].requestText, "apfelbaum");
        compare(items[2].mode, "summarize");
        compare(items[2].requestText, "apfelbaum");
        compare(items[2].clipboard, false);
        compare(items[3].mode, "summarize");
        compare(items[3].requestText, "");
        compare(items[3].clipboard, true);
    }

    function test_blank_query_shows_all_choices() {
        const items = launcher.getItems("");
        compare(items.length, 4);
        compare(items[0].mode, "ask");
        compare(items[1].mode, "translate");
        compare(items[2].mode, "summarize");
        compare(items[3].mode, "summarize");
        compare(items[0].requestText, "");
        compare(items[1].requestText, "");
        compare(items[2].requestText, "");
        compare(items[3].requestText, "");
        compare(items[3].clipboard, true);
    }

    function test_only_clipboard_summary_executes_without_input() {
        const items = launcher.getItems("");
        pluginServiceMock.lastRequest = null;
        launcher.executeItem(items[0]);
        compare(pluginServiceMock.lastRequest, null);
        launcher.executeItem(items[3]);
        verify(pluginServiceMock.lastRequest !== null);
        compare(pluginServiceMock.lastPluginId, "quickAi");
        compare(pluginServiceMock.lastVarName, "openRequest");
        compare(pluginServiceMock.lastRequest.mode, "summarize");
        compare(pluginServiceMock.lastRequest.text, "");
        compare(pluginServiceMock.lastRequest.clipboard, true);
    }

    name: "QuickAILauncher"

    QuickAI.QuickAILauncher {
        id: launcher

        pluginService: pluginServiceMock
    }

    QtObject {
        id: pluginServiceMock

        property var lastRequest: null
        property string lastPluginId: ""
        property string lastVarName: ""

        function setGlobalVar(pluginId, varName, value) {
            lastPluginId = pluginId;
            lastVarName = varName;
            lastRequest = value;
        }

    }

}
