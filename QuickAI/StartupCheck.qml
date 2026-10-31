import QtQuick
import qs.Common
import qs.Services

QtObject {
    function check(done) {
        const helperPath = Paths.strip(Qt.resolvedUrl("quick_ai.py"));
        Proc.runCommand("quickAi.check", ["python3", helperPath, "--check"], (output, exitCode) => {
            let result = null;
            try {
                result = JSON.parse(output.trim());
            } catch (error) {
                result = null;
            }
            if (exitCode !== 0 || !result || !result.ok) {
                done({
                    "title": "Quick AI could not start",
                    "details": result && result.message ? result.message : "Could not check the OpenRouter key. Confirm Python is installed and OPENROUTER_API_KEY is set, then enable the plugin again."
                });
                return ;
            }
            done(null);
        });
    }

}
