# abaqus-mcp

## Prerequisites

- Python >= 3.10 (system Python, not Abaqus built-in)
- Abaqus CAE 2024 or later (tested on 2025)
- Install dependencies:

```bash
  pip install -r requirements.txt
```

## Installation

### 1. Clone the repository in any directory(best for user's directory, or you have to set environment variable)
```bash
git clone https://github.com/pureSaviour/abaqus-mcp.git
```


### 2. Config abaqus plugin environment, so that each time you start the abaqus cae,the plugin will start automatically
```powershell
# Windows
Copy-Item "abaqus_plugin\abaqus_v6.env" $env:USERPROFILE
```
```bash
# Linux Macos
cp abaqus_plugin/abaqus_v6.env $HOME
```

### 3. Add mcp to your agent's config file (for claude code, it is ~/.claude.json)
```json
{
  "mcpServers": {
    "abaqus-mcp": {
      "command": "python",
      "args": [
        "your_root_path/mcp_server.py"
      ],
      "env": {
        "ABAQUS_MCP_HOME": "your_root_path"
      }
    }
  }
}
```

### 4. Set the ABAQUS_MCP_HOME environment variable
```powershell
# Windows (run as Administrator, or change "Machine" to "User")
[Environment]::SetEnvironmentVariable("ABAQUS_MCP_HOME", "your_root_path", "Machine")
```
```bash
# Linux/macOS (bash)
echo 'export ABAQUS_MCP_HOME="your_root_path"' >> ~/.bashrc
source ~/.bashrc

# macOS (zsh, default since macOS Catalina)
echo 'export ABAQUS_MCP_HOME="your_root_path"' >> ~/.zshrc
source ~/.zshrc
```

### 5. Start the abaqus cae, if your abaqus's **message area** has message like this:
```text
Executing "onCaeStartup()" in the home directory ...
=======================================================
 Abaqus MCP Plugin v*.*.*
=======================================================
 Home: your abaqus mcp root
 Start: mcp_start()  (background, recommended)
        mcp_loop()   (blocking, most compatible)
 Stop:  mcp_stop()
 Info:  mcp_status()
=======================================================
```
it means that plugin has loaded successfully