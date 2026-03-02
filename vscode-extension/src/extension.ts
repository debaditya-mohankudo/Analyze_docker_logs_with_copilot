import * as vscode from 'vscode';

/**
 * Docker Log Analyzer - Copilot Extension
 * Provides intelligent analysis of Docker container logs using VS Code Copilot
 */

class DockerLogAnalyzerAgent {
    private outputChannel: vscode.OutputChannel;
    private diagnosticCollection: vscode.DiagnosticCollection;

    constructor() {
        this.outputChannel = vscode.window.createOutputChannel('Docker Log Analyzer');
        this.diagnosticCollection = vscode.languages.createDiagnosticCollection('docker-logs');
    }

    /**
     * Main log analysis command - uses Copilot to analyze container logs
     */
    async analyzeContainerLogs() {
        this.outputChannel.clear();
        this.outputChannel.show();
        this.outputChannel.appendLine('🚀 Starting Docker Log Analysis with Copilot...\n');

        try {
            // Get user input for logs
            const logsInput = await vscode.window.showInputBox({
                placeHolder: 'Paste container logs here (or use sample data)',
                prompt: 'Enter Docker container logs to analyze',
                multiline: true,
                ignoreFocusOut: true
            });

            if (!logsInput) {
                this.outputChannel.appendLine('❌ Analysis cancelled');
                return;
            }

            // Prepare the analysis prompt
            const analysisPrompt = this.prepareAnalysisPrompt(logsInput);

            this.outputChannel.appendLine('📝 Prompt prepared. Sending to Copilot...\n');

            // Call Copilot
            const response = await this.callCopilot(analysisPrompt);

            this.outputChannel.appendLine('✅ Analysis Complete:\n');
            this.outputChannel.appendLine(response);

            // Show in quick pick for easy viewing
            await vscode.window.showInformationMessage(
                'Log analysis complete! Check the output panel.',
                'View Details'
            ).then(() => this.outputChannel.show());

        } catch (error) {
            this.handleError('Log analysis failed', error);
        }
    }

    /**
     * Discover log patterns (timestamp formats, language, health checks)
     */
    async discoverPatterns() {
        this.outputChannel.clear();
        this.outputChannel.show();
        this.outputChannel.appendLine('🔬 Analyzing log patterns...\n');

        try {
            const logsInput = await vscode.window.showInputBox({
                placeHolder: 'Paste logs to analyze patterns',
                prompt: 'Enter Docker logs for pattern discovery',
                multiline: true,
                ignoreFocusOut: true
            });

            if (!logsInput) {
                this.outputChannel.appendLine('❌ Analysis cancelled');
                return;
            }

            const patternPrompt = this.preparePatternPrompt(logsInput);
            const response = await this.callCopilot(patternPrompt);

            this.outputChannel.appendLine('✅ Pattern Analysis:\n');
            this.outputChannel.appendLine(response);

            // Also show a summary
            this.outputChannel.appendLine('\n📊 Quick Summary:');
            this.outputChannel.appendLine('- Check language detection');
            this.outputChannel.appendLine('- Verify timestamp format');
            this.outputChannel.appendLine('- Review health check patterns');
            this.outputChannel.appendLine('- Analyze error distributions');

        } catch (error) {
            this.handleError('Pattern analysis failed', error);
        }
    }

    /**
     * Test with realistic sample logs
     */
    async testWithSampleLogs() {
        this.outputChannel.clear();
        this.outputChannel.show();
        this.outputChannel.appendLine('🧪 Testing with realistic sample logs...\n');

        const sampleLogs = this.generateSampleLogs();

        try {
            this.outputChannel.appendLine('📦 Sample logs being analyzed:\n');
            this.outputChannel.appendLine(sampleLogs.substring(0, 500) + '...\n');

            const analysisPrompt = this.prepareAnalysisPrompt(sampleLogs);
            const response = await this.callCopilot(analysisPrompt);

            this.outputChannel.appendLine('✅ Analysis Results:\n');
            this.outputChannel.appendLine(response);

        } catch (error) {
            this.handleError('Sample data test failed', error);
        }
    }

    /**
     * Call Copilot via Language Model API
     */
    private async callCopilot(prompt: string): Promise<string> {
        try {
            // Request Copilot models
            const models = await vscode.lm.selectChatModels({
                vendor: 'copilot'
            });

            if (models.length === 0) {
                throw new Error('No Copilot models available. Ensure GitHub Copilot extension is installed and enabled.');
            }

            const model = models[0];
            this.outputChannel.appendLine(`📡 Using model: ${model.id}`);

            // Create chat messages
            const messages: vscode.LanguageModelChatMessage[] = [
                vscode.LanguageModelChatMessage.User(prompt)
            ];

            // Send request
            const token = new vscode.CancellationTokenSource().token;
            const response = await model.sendRequest(messages, {}, token);

            // Collect response text
            let fullResponse = '';
            for await (const chunk of response.text) {
                fullResponse += chunk;
            }

            return fullResponse;

        } catch (error) {
            if (error instanceof vscode.LanguageModelError) {
                throw new Error(`Copilot API error: ${error.message} (${error.code})`);
            }
            throw error;
        }
    }

    /**
     * Prepare analysis prompt for Copilot
     */
    private prepareAnalysisPrompt(logs: string): string {
        return `You are an expert DevOps engineer analyzing Docker container logs to find error correlations.

Analyze these container logs for:
1. **Error patterns** - What types of errors appear and how often?
2. **Correlation** - Are there logs from multiple containers showing related failures?
3. **Timeline** - What's the sequence of events leading to the error?
4. **Root cause** - What's the most likely root cause?
5. **Recommendations** - What should be fixed or monitored?

Format your response as:
## Error Analysis
- List each error found

## Correlation Analysis
- Show relationships between errors

## Root Cause Assessment
- Most likely cause

## Recommendations
- Action items

---

LOGS TO ANALYZE:
\`\`\`
${logs}
\`\`\`

Provide a concise, actionable analysis.`;
    }

    /**
     * Prepare pattern discovery prompt
     */
    private preparePatternPrompt(logs: string): string {
        return `Analyze these Docker container logs and identify:

1. **Timestamp Format** - What format are the timestamps in? (ISO-8601, syslog, epoch, custom?)
2. **Programming Language** - What language/framework are these logs from? (Python, Java, PHP, Node.js, Go?)
3. **Health Check Patterns** - Are there repeating health check logs? What's the pattern?
4. **Log Levels** - What log levels are present? (INFO, DEBUG, ERROR, WARNING, CRITICAL?)
5. **Error Types** - What types of errors appear? (Connection errors, timeouts, exceptions?)

Format response as JSON:
\`\`\`json
{
  "timestamp_format": "iso8601 | syslog | epoch | apache | custom",
  "language": "python | java | php | nodejs | go | unknown",
  "health_check": {
    "detected": true/false,
    "pattern": "health check pattern here",
    "frequency": "per minute estimate"
  },
  "log_levels": ["INFO", "ERROR", ...],
  "error_types": ["Connection refused", ...]
}
\`\`\`

---

LOGS TO ANALYZE:
\`\`\`
${logs}
\`\`\`

Respond with only the JSON analysis, no additional text.`;
    }

    /**
     * Generate realistic sample logs for testing
     */
    private generateSampleLogs(): string {
        return `2024-03-02T21:19:41.123Z [backend-1] INFO Application started on 0.0.0.0:8000
2024-03-02T21:19:42.456Z [backend-1] DEBUG Database connection: postgresql://db:5432
2024-03-02T21:19:43.789Z [frontend-1] INFO Frontend server listening on port 3000
2024-03-02T21:19:44.101Z [backend-1] INFO Health check passed
2024-03-02T21:19:45.202Z [cache-1] INFO Redis client connected
2024-03-02T21:19:46.303Z [backend-1] INFO Health check passed
2024-03-02T21:20:01.111Z [backend-1] ERROR Database connection refused: Connection timeout after 30s
2024-03-02T21:20:01.222Z [backend-1] ERROR Failed to execute query: no active database connection
2024-03-02T21:20:01.333Z [cache-1] ERROR Unable to cache result: upstream service unavailable
2024-03-02T21:20:01.444Z [frontend-1] ERROR Failed to fetch data from /api/users: 503 Service Unavailable
2024-03-02T21:20:01.555Z [api-gateway] WARNING Health check endpoint returned status 503
2024-03-02T21:20:02.666Z [backend-1] ERROR Maximum retries exceeded connecting to database
2024-03-02T21:20:02.777Z [frontend-1] ERROR Request timeout: POST /api/users took >30s
2024-03-02T21:20:03.888Z [cache-1] INFO Reconnection attempt 1/5
2024-03-02T21:20:04.999Z [db] CRITICAL Database service is down - emergency mode activated
2024-03-02T21:20:05.111Z [backend-1] CRITICAL Cannot start without database - exiting
2024-03-02T21:20:06.222Z [frontend-1] INFO Fallback to cached data
2024-03-02T21:20:07.333Z [db] INFO Database recovered - resuming normal operations
2024-03-02T21:20:08.444Z [backend-1] INFO Reconnected to database
2024-03-02T21:20:09.555Z [backend-1] INFO Health check passed
2024-03-02T21:20:10.666Z [cache-1] INFO Connection restored`;
    }

    /**
     * Handle errors gracefully
     */
    private handleError(title: string, error: any) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        this.outputChannel.appendLine(`\n❌ ${title}`);
        this.outputChannel.appendLine(`Error: ${errorMessage}`);

        vscode.window.showErrorMessage(
            `${title}: ${errorMessage}`,
            'View Details'
        ).then(() => this.outputChannel.show());
    }

    dispose() {
        this.outputChannel.dispose();
        this.diagnosticCollection.dispose();
    }
}

// Global agent instance
let agent: DockerLogAnalyzerAgent;

/**
 * Extension activation
 */
export function activate(context: vscode.ExtensionContext) {
    console.log('🚀 Docker Log Analyzer - Copilot Extension activated');

    // Create agent
    agent = new DockerLogAnalyzerAgent();

    // Register commands
    const analyzeCommand = vscode.commands.registerCommand(
        'docker-log-analyzer.analyzeContainerLogs',
        () => agent.analyzeContainerLogs()
    );

    const discoverCommand = vscode.commands.registerCommand(
        'docker-log-analyzer.discoverPatterns',
        () => agent.discoverPatterns()
    );

    const testCommand = vscode.commands.registerCommand(
        'docker-log-analyzer.testWithSampleLogs',
        () => agent.testWithSampleLogs()
    );

    // Subscribe to disposables
    context.subscriptions.push(analyzeCommand);
    context.subscriptions.push(discoverCommand);
    context.subscriptions.push(testCommand);

    // Show welcome message
    vscode.window.showInformationMessage(
        '✅ Docker Log Analyzer with Copilot is ready! Use Ctrl+Shift+P to access commands.'
    );
}

/**
 * Extension deactivation
 */
export function deactivate() {
    if (agent) {
        agent.dispose();
    }
}
