#!/bin/bash
# REA CLI with the Android providers configured (same env as the MCP registration).
T='C:\Users\Jenwein\tools\rea-deps'
export REA_JADX_MCP_JAR="$T\jadx-headless-mcp-0.7.1-all.jar"
export JAVA_HOME="$T\jdk\jdk-21.0.12.1+1"
export REA_JADX_HEAP_MIB=16384
export REA_ADB_PATH="$T\platform-tools\adb.exe"
export REA_APKTOOL_COMMAND="$T\apktool.exe"
exec "/c/nvm4w/nodejs/node.exe" 'C:\nvm4w\nodejs\node_modules\rea-agents\scripts\rea.mjs' "$@"
