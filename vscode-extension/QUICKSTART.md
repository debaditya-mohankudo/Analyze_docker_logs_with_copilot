# VS Code Copilot Extension - Quick Start Guide

## ⚡ 5-Minute Setup

### Step 1: Prerequisites Check
Before starting, ensure you have:
- ✅ VS Code 1.92.0+
- ✅ GitHub Copilot extension installed
- ✅ GitHub Copilot enabled in VS Code
- ✅ Node.js 16+ installed

### Step 2: Install Dependencies
```bash
cd vscode-extension
npm install
```

### Step 3: Compile TypeScript
```bash
npm run compile
```

### Step 4: Launch Debug Instance
Press **F5** in VS Code (with extension folder open)

This opens a new VS Code window with the extension loaded.

---

## 🧪 Test the Extension (In Debug Window)

### Test 1: Sample Logs Analysis (Easiest)
1. Press **Ctrl+Shift+P** (or **Cmd+Shift+P** on Mac)
2. Type: `Docker Logs: Test with Sample Logs`
3. Press Enter
4. Check the **Output** panel (right side) for results

Expected: ~30 lines of analysis showing error correlation

### Test 2: Analyze Custom Logs
1. Press **Ctrl+Shift+P**
2. Type: `Docker Logs: Analyze with Copilot`
3. Paste these logs:
```
2024-03-02T21:20:01Z [db] CRITICAL Database service down
2024-03-02T21:20:01Z [api] ERROR Cannot connect to database
2024-03-02T21:20:02Z [frontend] ERROR API returned 503
```
4. Press Enter
5. View output in **Output** panel

Expected: Identification of cascading failure

### Test 3: Pattern Discovery
1. Press **Ctrl+Shift+P**
2. Type: `Docker Logs: Discover Patterns`
3. Paste the same logs
4. View JSON output in **Output** panel

Expected: JSON with detected timestamp format, language, etc.

---

## 📊 Understanding the Output

The extension writes to the **"Docker Log Analyzer"** output channel.

**What you'll see:**
```
🚀 Starting Docker Log Analysis with Copilot...

📝 Prompt prepared. Sending to Copilot...

📡 Using model: copilot

✅ Analysis Complete:

## Error Analysis
- [list of errors found]

## Correlation Analysis
- [relationships between errors]

## Root Cause Assessment
- [most likely cause]

## Recommendations
- [action items]
```

---

## 🔄 Development Workflow

### File Editing
If you edit `src/extension.ts`:

1. Press **Ctrl+Shift+B** to compile
2. Reload the debug window (**Ctrl+R**)
3. Test your changes

### Watch Mode
For faster iteration:
```bash
npm run watch
```

This automatically recompiles on file changes. Still need to reload debug window.

---

## 🐛 Common Issues

### Issue: "No Copilot models available"
**Solution:**
1. Check Copilot is installed (Extensions panel in VS Code)
2. Check Copilot is enabled: VS Code Settings → Search "copilot" → Ensure enabled
3. Restart VS Code main window, then reopen debug window

### Issue: Command doesn't appear in Ctrl+Shift+P menu
**Solution:**
1. Run `npm run compile`
2. Reload debug window (Ctrl+R)
3. Try again

### Issue: Extension crashes or shows errors
**Solution:**
1. Check extension output: **Help → Toggle Output Channel → Docker Log Analyzer**
2. Look for error messages
3. Check main VS Code console (F12 in main window)

---

## 📝 Test Data

### Sample Multi-Container Logs
Use this for testing:
```
2024-03-02T21:19:41Z [backend-1] INFO App started
2024-03-02T21:19:45Z [backend-1] INFO Health check OK
2024-03-02T21:20:01Z [backend-1] ERROR DB connection timeout
2024-03-02T21:20:01Z [frontend-1] ERROR API returned 503
2024-03-02T21:20:01Z [cache-1] ERROR Connection refused
2024-03-02T21:20:05Z [db] INFO Service recovered
2024-03-02T21:20:06Z [backend-1] INFO Reconnected
```

### Python Logs
```
2024-03-02 21:20:01,123 - app - ERROR - Connection refused
Traceback (most recent call last):
  File "/app/main.py", line 42, in connect
    socket.connect(host, port)
ConnectionRefusedError: [Errno 111] Connection refused
```

### Java Logs
```
2024-03-02 21:20:01.123 [main] ERROR Exception in thread
java.lang.NullPointerException
    at com.example.App.main(App.java:42)
Caused by: java.sql.SQLException: Cannot connect to database
```

---

## 🚀 Next Steps

1. **Experiment with different logs** - Test with your actual container logs
2. **Compare analyses** - Try same logs, see how Copilot analyzes them
3. **Validate patterns** - Check if discovered patterns make sense
4. **Package for team** - Run `vsce package` to create `.vsix` file
5. **Integrate with analyzer** - Use insights to improve main project heuristics

---

## 📖 Command Reference

| Command | Shortcut | Use Case |
|---------|----------|----------|
| **Analyze with Copilot** | Ctrl+Shift+P → type name | Error correlation analysis |
| **Discover Patterns** | Ctrl+Shift+P → type name | Learn log characteristics |
| **Test Sample Data** | Ctrl+Shift+P → type name | Verify setup works |

---

## ✨ Tips for Best Results

1. **Provide context** - More logs = better analysis (~100+ lines recommended)
2. **Include errors** - Copilot works best when there are actual errors to find
3. **Multi-container** - Provide logs from multiple containers to see correlation power
4. **Full timeline** - Include logs leading up to and after the error

---

## 🎓 Understanding the Analysis

### What Copilot Looks For:
- Errors that happen at the same time
- Services that fail in sequence (cascading failures)
- Related error messages (e.g., "connection refused" then "cannot fetch data")
- Timestamp patterns that show causation

### Example Analysis:
```
User Request (Frontend)
         ↓
API Call Timeout (21:20:01)
         ↓
Backend Cannot Connect to DB (21:20:01)
         ↓
Database Service Down (21:20:00)

ROOT CAUSE: Database went down first, cascading to all dependent services
RECOMMENDATION: Implement database health checks with faster failover
```

---

Happy testing! 🎉
