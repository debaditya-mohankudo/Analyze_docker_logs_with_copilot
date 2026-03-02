# Docker Log Analyzer - Copilot Extension

A VS Code extension that uses Copilot AI to analyze Docker container logs and find error correlations directly within your development environment.

## ✨ Features

- **Copilot-Powered Analysis** - Uses VS Code's built-in Copilot to analyze logs
- **Multi-Container Correlation** - Finds relationships between errors across containers
- **Pattern Discovery** - Detects timestamp formats, programming languages, health checks
- **Local Development** - No API keys needed, works with VS Code Copilot
- **Real-time Testing** - Paste logs directly into VS Code for instant analysis
- **Sample Data** - Test with realistic Docker logs out of the box

## 🎯 Commands

### Docker Logs: Analyze with Copilot
Analyze container logs using Copilot to identify error correlations and root causes.

**How to use:**
1. Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac)
2. Type: `Docker Logs: Analyze with Copilot`
3. Paste your container logs
4. View analysis in the output panel

**Example output:**
```
## Error Analysis
- Database connection timeout at 21:20:01
- Frontend timeout waiting for response at 21:20:01
- Cache connection refused at 21:20:01

## Correlation Analysis
- All errors occur within same 2-second window
- Database failure cascades to frontend and cache

## Root Cause Assessment
- Primary: Database service shutdown
- Secondary: Retry logic caused frontend timeout

## Recommendations
- Implement database health checks with faster failover
- Add circuit breaker for database connections
```

### Docker Logs: Discover Patterns
Analyze log patterns to understand container characteristics.

**Output includes:**
- Timestamp format (ISO-8601, syslog, epoch)
- Programming language (Python, Java, PHP, Go, Node.js)
- Health check patterns and frequency
- Log level distribution
- Common error types

**Example:**
```json
{
  "timestamp_format": "iso8601",
  "language": "python",
  "health_check": {
    "detected": true,
    "pattern": "Health check passed",
    "frequency": "1 per second"
  },
  "log_levels": ["INFO", "DEBUG", "ERROR", "WARNING"],
  "error_types": ["Connection refused", "Timeout"]
}
```

### Docker Logs: Test with Sample Data
Test the extension with realistic multi-container logs showing cascading failures.

Perfect for understanding how the analyzer detects error correlations.

## 🚀 Installation & Setup

### Prerequisites
- VS Code 1.92.0 or later
- GitHub Copilot extension installed and enabled
- GitHub Copilot subscription (free tier may be available)

### Development Setup

1. **Clone/navigate to the extension directory:**
   ```bash
   cd vscode-extension
   ```

2. **Install dependencies:**
   ```bash
   npm install
   ```

3. **Compile TypeScript:**
   ```bash
   npm run compile
   ```

4. **Test locally:**
   - Press `F5` in VS Code to open extension debug window
   - Run any of the commands above in the debug instance

### Packaging for Distribution

```bash
# Install vsce if not already installed
npm install -g vsce

# Package the extension
vsce package

# This creates: docker-log-analyzer-copilot-0.1.0.vsix
```

## 🧪 Testing

### Test 1: Analyze with Copilot
1. Run command `Docker Logs: Analyze with Copilot`
2. Paste these logs:
   ```
   2024-03-02T21:20:01Z [backend] ERROR Database connection refused
   2024-03-02T21:20:01Z [frontend] ERROR Failed to fetch /api/users: 503
   2024-03-02T21:20:01Z [cache] ERROR Cache request timeout
   ```
3. Copilot should identify the cascade failure pattern

### Test 2: Pattern Discovery
1. Run command `Docker Logs: Discover Patterns`
2. Paste the same logs
3. Should output JSON with detected patterns

### Test 3: Sample Data
1. Run command `Docker Logs: Test with Sample Logs`
2. Should show analysis of realistic multi-container failure scenario
3. Output should clearly show error correlation timeline

## 🔄 Comparison: Extension vs Cloud LLM

| Aspect | Extension | OpenAI Cloud |
|--------|-----------|--------------|
| **Cost** | Free (with Copilot subscription) | $0.01-0.10 per request |
| **Latency** | <1s | 2-5s |
| **Privacy** | Local (VS Code) | Sent to OpenAI |
| **API Keys** | None needed | OPENAI_API_KEY |
| **Deployment** | VS Code only | Anywhere (Docker, cloud) |
| **Development** | Fast iteration | Simple but costly |

## 📖 Usage Scenarios

### Scenario 1: Local Development Testing
```
Developer → Container logs → Extension → Copilot → Analysis
                                        (No API cost!)
```

### Scenario 2: Production Debugging
Copy logs from production environment → Paste in extension → Instant analysis
Perfect for rapid debugging without waiting for API calls.

### Scenario 3: Learning Log Analysis
Use "Test with Sample Data" to understand error correlation patterns
Helper for team onboarding.

## 🔧 Integration with Main Project

The extension analyzes logs independently. To use with the main analyzer:

1. **During Development:**
   - Use extension for quick iteration
   - No need to start Docker/Kafka stack
   - Get instant Copilot feedback

2. **Pattern Discovery Phase:**
   - Current Python module generates `container_patterns.json`
   - Extension can validate patterns via Copilot
   - Refine heuristics based on Copilot insights

3. **Transition to Production:**
   - Extension for dev/testing
   - Cloud LLM (OpenAI/MCP) for production
   - Hybrid: Use extension for pattern validation first

## 🐛 Troubleshooting

### "No Copilot models available"
- Ensure GitHub Copilot extension is installed
- Check that Copilot is enabled in VS Code settings
- Verify you have active Copilot subscription

### "Model error: off_topic"
- Copilot thinks logs aren't relevant
- Provide more context in the logs
- Try broader analysis request

### Extension not appearing in command palette
- Run `npm run compile` to rebuild
- Reload VS Code window (Ctrl+R)
- Check extension output panel for errors

## 📝 Architecture

```
VS Code Extension (TypeScript)
    ↓
Copilot Language Model API
    ↓
Large Language Model (Copilot's backend)
    ↓
Analysis Results
    ↓
Output Channel (displayed in VS Code)
```

## 🎓 Next Steps

1. **Test locally** - Use F5 to debug and iterate
2. **Validate Copilot insights** - Compare with pattern heuristics
3. **Integrate with main analyzer** - Use extension for local dev
4. **Package and share** - Create `.vsix` for team use

## 📄 Files

- `src/extension.ts` - Main extension logic
- `package.json` - Extension manifest
- `tsconfig.json` - TypeScript configuration
- `.gitignore` - Git ignore rules

## 🔗 Resources

- [VS Code Extension API](https://code.visualstudio.com/api)
- [Copilot in VS Code](https://code.visualstudio.com/docs/copilot/overview)
- [Language Model API](https://code.visualstudio.com/api/references/vscode-api#lm)

## 📝 License

MIT
