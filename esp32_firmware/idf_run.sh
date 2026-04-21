#!/bin/bash

# --- ANSI Colors ---
GREEN="\033[92m"
YELLOW="\033[93m"
BLUE="\033[94m"
RESET="\033[0m"
BOLD="\033[1m"

echo -e "${BOLD}${BLUE}========================================${RESET}"
echo -e "${BOLD}${BLUE}      ESP-IDF ENVIRONMENT WRAPPER       ${RESET}"
echo -e "${BOLD}${BLUE}========================================${RESET}"

# 1. Safely disconnect the Host Python Virtual Environment (if active)
if [ -n "$VIRTUAL_ENV" ]; then
    echo -e "${YELLOW}[*] Temporarily detaching host virtual environment: $(basename $VIRTUAL_ENV)...${RESET}"
    # Remove the venv's bin path from the current PATH variable
    export PATH=$(echo $PATH | tr ":" "\n" | grep -v "$VIRTUAL_ENV" | paste -sd ":" -)
    unset VIRTUAL_ENV
fi

# 2. Silently source the ESP-IDF environment
echo -e "${GREEN}[*] Injecting ESP-IDF Toolchain...${RESET}"
source ~/esp/esp-idf/export.sh > /dev/null 2>&1

if [ $? -ne 0 ]; then
    echo -e "\033[91m[Error] Failed to load ESP-IDF. Did you run the install script?${RESET}"
    exit 1
fi

echo -e "${GREEN}[✔] ESP32 Environment Ready.${RESET}\n"

# 3. Execute logic based on user input
if [ $# -eq 0 ]; then
    # If no arguments were passed, spawn a new interactive shell loaded with ESP tools
    echo -e "${YELLOW}Entering ESP-IDF Shell. Type 'exit' to return to your normal PyCharm environment.${RESET}\n"
    exec bash
else
    # If arguments were passed (e.g. ./idf_run.sh build), pass them directly to idf.py
    idf.py "$@"
fi