#!/usr/bin/env python3

import os
import sys
import subprocess
import json
import argparse
import tempfile
import shlex
import re
import logging # Import logging
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from rich.console import Console
from rich.text import Text
from rich.prompt import Confirm, Prompt

# --- Basic Logging Setup ---
# Configure logging format and default level to WARNING
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
# Get the root logger
log = logging.getLogger()

# --- Configuration ---
load_dotenv() # Load variables from .env file

APP_NAME = 'commander'

# --- OpenAI Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = "gpt-4o-mini"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

# --- Prompt Configuration ---
SYSTEM_PROMPT = """You are an expert Linux user acting as a translator. Your task is to translate the user's natural language query into a single, valid, and safe Linux shell command. Use the provided context (working directory, files, OS info, potentially user-selected recent history) to create the most relevant command.
VERY IMPORTANT: Respond ONLY with the raw shell command text. Do not include any explanations, markdown formatting (like ```), apologies, or other text. Just the command."""
USER_PROMPT_TEMPLATE = "Context:\n{context}\n\nUser Query:\n{query}\n\nGenerate Command:"

# --- Context Configuration ---
HISTORY_LINES = 15
HISTORY_FILTER_PATTERN = r'^\s*(export|history|source .*/commander\.py|.*/commander\.py)\s+'

# --- Other Config ---
EDITOR = os.getenv("EDITOR", "nano")

# --- Initialize Rich Console ---
console = Console()

# --- Functions ---

def check_dependencies():
    pass

def run_subprocess(cmd_list, capture=True, check=False, text=True, timeout=10):
    """Helper to run subprocesses and handle errors."""
    log.debug(f"Running subprocess: {' '.join(map(shlex.quote, cmd_list))}")
    try:
        # --- CORRECTED LINE ---
        # Ensure NO explicit stderr or stdout when capture_output=True
        result = subprocess.run(
            cmd_list,
            capture_output=capture, # This sets stdout=PIPE, stderr=PIPE internally
            check=check,
            text=text,
            timeout=timeout
            # NO stderr=subprocess.PIPE HERE
        )
        # --- END CORRECTION ---

        # Handling based on 'check' flag
        if check and result.returncode != 0:
             # This condition is technically redundant if check=True raises CalledProcessError,
             # but acts as a safeguard or if check=False is used differently later.
             log.error(f"Command failed (exit code {result.returncode}): {' '.join(cmd_list)}\nStderr: {result.stderr}")
             return f"Error running {' '.join(cmd_list)}"

        # Return based on 'capture' flag
        if capture:
             log.debug(f"Subprocess exited with {result.returncode}. Stdout captured.")
             # Check stderr even on success if capturing, could contain warnings
             if result.stderr:
                 log.debug(f"Subprocess stderr: {result.stderr.strip()}")
             return result.stdout.strip()
        else:
             log.debug(f"Subprocess exited with {result.returncode}.")
             return result.returncode # Return exit code if not capturing stdout

    except FileNotFoundError:
        log.error(f"Command not found: {cmd_list[0]}")
        console.print(f"[bold red]Error:[/bold red] Command not found: {cmd_list[0]}")
        return f"Error running {cmd_list[0]}"
    except subprocess.TimeoutExpired:
        log.error(f"Command timed out: {' '.join(cmd_list)}")
        console.print(f"[bold red]Error:[/bold red] Command timed out: {' '.join(cmd_list)}")
        return f"Error: Timeout running {' '.join(cmd_list)}"
    except subprocess.CalledProcessError as e: # Happens when check=True and exit code != 0
        log.error(f"Command failed: {' '.join(cmd_list)}\nStderr: {e.stderr}")
        # Don't necessarily print to console here, let caller decide? Or keep it.
        # console.print(f"[bold red]Error:[/bold red] Command failed: {' '.join(cmd_list)}\n{e.stderr}")
        return f"Error running {' '.join(cmd_list)}"
    except ValueError as e: # Catch the specific argument error
         log.exception(f"ValueError running subprocess {' '.join(cmd_list)} (check arguments like capture_output/stdout/stderr)")
         console.print(f"[bold red]Internal Error:[/bold red] Subprocess configuration error for {' '.join(cmd_list)}: {e}")
         return f"Error configuring subprocess for {' '.join(cmd_list)}"
    except Exception as e:
        log.exception(f"Unexpected error running {' '.join(cmd_list)}")
        console.print(f"[bold red]Error:[/bold red] Unexpected error running {' '.join(cmd_list)}: {e}")
        return f"Error running {' '.join(cmd_list)}"

def collect_context():
    """Collects shell context (pwd, ls, uname, whoami, filtered history)."""
    context = {}
    log.debug("Starting context collection.")
    console.print("Collecting context...", style="dim")
    context['pwd'] = os.getcwd()
    log.debug(f"pwd: {context['pwd']}")
    context['ls -al'] = run_subprocess(['ls', '-al'])
    log.debug(f"ls -al: {context['ls -al'][:200]}...")
    context['uname -a'] = run_subprocess(['uname', '-a'])
    log.debug(f"uname -a: {context['uname -a']}")
    context['whoami'] = run_subprocess(['whoami'])
    log.debug(f"whoami: {context['whoami']}")

    # --- History Collection ---
    potential_history = []
    try:
        history_cmd_str = f"history {HISTORY_LINES}"
        log.debug(f"Attempting history collection with command string: '{history_cmd_str}'")
        history_raw = subprocess.run(
            history_cmd_str, shell=True, capture_output=True, text=True,
            timeout=5, check=True
        ).stdout.strip()

        log.debug(f"Raw history output (first 200 chars): {history_raw[:200]}...")
        filtered_lines = []
        for line in history_raw.splitlines():
            line_content = re.sub(r'^\s*\d+\s+', '', line).strip()
            if line_content and not re.match(HISTORY_FILTER_PATTERN, line_content):
                filtered_lines.append(line_content)
            else:
                 log.debug(f"Filtered out history line: {line}")
        potential_history = filtered_lines
        log.debug(f"Filtered history lines: {potential_history}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.warning(f"Could not get shell history via '{history_cmd_str}' command: {e}")
        potential_history = []
    except FileNotFoundError:
         log.warning("The 'history' command seems unavailable in this environment.")
         potential_history = []
    except Exception as e:
        log.exception("Unexpected error during history collection.")
        console.print(f"[yellow]Warning:[/yellow] Could not reliably get shell history: {e}")
        potential_history = []

    context['potential_history'] = potential_history
    console.print(f"Context collected ({len(potential_history)} potential history lines).", style="dim")
    log.debug("Finished context collection.")
    return context


def prompt_for_history_selection(potential_history):
    """Interactively prompts the user to select history lines."""
    if not potential_history:
        log.debug("No potential history to prompt for.")
        console.print("No relevant history found to include.", style="dim")
        return []

    console.print("--- Recent (filtered) History ---", style="bold blue")
    for i, line in enumerate(potential_history):
        console.print(f"{i+1: >2}: {line}")
    console.print("---", style="bold blue")

    lines_to_include = []
    while True:
        selection = Prompt.ask(
            "Include history lines (e.g., 1,3,5), 'a' for all, 'n' for none",
            default="n"
        ).strip().lower()
        log.debug(f"User history selection input: '{selection}'")

        if selection == 'a':
            lines_to_include = potential_history
            break
        elif selection == 'n' or not selection:
            lines_to_include = []
            break
        elif selection:
            try:
                indices = [int(i.strip()) - 1 for i in selection.split(',') if i.strip()]
                valid_indices = [i for i in indices if 0 <= i < len(potential_history)]
                invalid_indices = [i+1 for i in indices if not (0 <= i < len(potential_history))]
                if invalid_indices:
                     log.warning(f"Ignoring invalid history indices: {invalid_indices}")
                     console.print(f"[yellow]Warning:[/yellow] Ignoring invalid numbers: {invalid_indices}")

                lines_to_include = [potential_history[i] for i in valid_indices]
                lines_to_include = list(dict.fromkeys(lines_to_include))
                log.debug(f"Selected history indices: {valid_indices}")
                break
            except ValueError:
                log.warning("Invalid input format for history selection.")
                console.print("[bold red]Invalid input.[/bold red] Please enter numbers separated by commas, 'a', or 'n'.")
        # Loop continues if input was invalid

    # Use log.info for significant actions, log.debug for finer detail
    log.info(f"Selected {len(lines_to_include)} history lines for context.")
    if lines_to_include:
         console.print(f"Including {len(lines_to_include)} history line(s) in context.", style="dim")
    else:
         console.print("Including no history in context.", style="dim")
    return lines_to_include


def get_command_from_openai(context_dict, query, model):
    """Sends context and query to OpenAI, returns the suggested command."""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        log.debug("OpenAI client initialized.")
    except Exception as e:
         log.exception("Failed to initialize OpenAI client")
         console.print(f"[bold red]Error:[/bold red] Failed to initialize OpenAI client: {e}")
         return None

    context_string = json.dumps(context_dict, indent=2)
    user_prompt_content = USER_PROMPT_TEMPLATE.format(context=context_string, query=query)
    log.debug(f"Using model: {model}")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt_content},
        ],
        "temperature": 0.2,
        "max_tokens": 200
    }
    log.debug(f"OpenAI request payload (excluding content): {{'model': '{model}', 'temperature': 0.2, 'max_tokens': 200}}")

    console.print(f"Asking AI (Model: {model})...", style="dim")
    try:
        response = client.chat.completions.create(**payload)
        log.debug(f"Raw OpenAI response object: {response}")

        # Log HTTP request/response details if needed (can be verbose)
        # Requires inspecting headers/status from the underlying http client if using openai lib v1+
        # For now, just log success based on reaching this point without error
        log.info(f"OpenAI API call successful (Model: {model})") # Log success at INFO level

        command = response.choices[0].message.content
        if not command:
             log.error("AI returned an empty response content.")
             console.print("[bold red]Error:[/bold red] AI returned an empty response.")
             return None

        original_command = command
        command = command.strip().strip('`').strip()
        # Log suggested commands at INFO level
        log.info(f"AI suggested command (raw): '{original_command}'")
        log.info(f"AI suggested command (cleaned): '{command}'")


        if command and not re.match(r'^[a-zA-Z0-9_./~-]', command):
            log.warning(f"AI command starts with potentially unsafe characters: '{command[:20]}...'")
            console.print(f"[yellow]Warning:[/yellow] AI returned potentially unsafe command start: '{command[:20]}...'")

        return command

    except OpenAIError as e:
        log.exception("OpenAI API Error occurred")
        console.print(f"[bold red]Error:[/bold red] OpenAI API Error: {e}")
        return None
    except Exception as e:
        log.exception("Failed to get response from OpenAI")
        console.print(f"[bold red]Error:[/bold red] Failed to get response from OpenAI: {e}")
        return None


def edit_command_in_editor(command):
    """Opens the command in the user's editor for modification."""
    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=".sh") as tf:
            tf.write(command + '\n')
            temp_file_path = tf.name
        log.debug(f"Opening editor '{EDITOR}' for temporary file: {temp_file_path}")

        editor_cmd = shlex.split(EDITOR)
        status = run_subprocess(editor_cmd + [temp_file_path], capture=False)

        if status == 0:
            with open(temp_file_path, 'r') as tf:
                edited_command = tf.read().strip()
            log.info(f"Command edited. New command: '{edited_command}'")
            return edited_command
        else:
            log.warning(f"Editor exited with non-zero status {status}. Assuming no changes.")
            console.print(f"[yellow]Warning:[/yellow] Editor exited with status {status}. Using original command.")
            return command

    except Exception as e:
        log.exception("Failed to open or process editor")
        console.print(f"[bold red]Error:[/bold red] Failed to open editor: {e}")
        return command
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            log.debug(f"Deleting temporary file: {temp_file_path}")
            os.unlink(temp_file_path)


def run_command_with_confirmation(command_to_run, query):
    """Handles the confirmation and execution loop."""
    current_command = command_to_run
    is_interactive_term = sys.stdin.isatty() and sys.stdout.isatty()

    while True:
        log.debug(f"Presenting command for confirmation: '{current_command}'")
        console.print("--- Suggested Command ---", style="bold cyan")
        console.print(current_command, style="bold")
        console.print("---", style="bold cyan")

        if not is_interactive_term:
             log.warning("Not interactive terminal, skipping execution confirmation.")
             console.print("[yellow]Warning:[/yellow] Not an interactive terminal. Cannot ask for execution confirmation.")
             console.print("Printing command only.")
             break

        try:
             action = Prompt.ask(
                 "[bold yellow]Execute this command?[/bold yellow]",
                 choices=["y", "n", "e"],
                 default="n"
             ).lower()
             log.debug(f"User confirmation action: '{action}'")
        except EOFError:
             action = "n"
             log.info("User aborted confirmation via EOF.")
             console.print("\nAborted.")


        if action == "y":
            log.info(f"User confirmed execution for command: '{current_command}'")
            console.print("Executing...", style="dim")

            exit_code = run_subprocess(
                [current_command],
                capture=False,
                check=False,
                shell=True
            )
            log.info(f"Command execution finished with exit code: {exit_code}")
            console.print(f"--- Command finished (Exit Code: {exit_code}) ---", style="dim")
            sys.exit(exit_code)

        elif action == "e":
            log.info("User chose to edit the command.")
            edited = edit_command_in_editor(current_command)
            if edited and edited != current_command:
                current_command = edited
                log.debug("Command was edited, looping for confirmation.")
            elif edited == current_command:
                 log.info("Edit resulted in no changes to the command.")
                 console.print("No changes detected.", style="dim")
            else:
                 log.warning("Edit process cancelled or failed.")
                 console.print("Edit cancelled or failed. Aborting execution.", style="dim")
                 sys.exit(1)
        else: # n or any other input
             log.info("User aborted execution.")
             console.print("Execution aborted by user.", style="dim")
             sys.exit(0)


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Translates natural language queries into Linux shell commands using OpenAI.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '-x', '--execute',
        action='store_true',
        help='Enable execution mode. Prompts for confirmation [y/N/e] before running.'
    )
    parser.add_argument(
        '--model',
        type=str,
        default=OPENAI_MODEL,
        help=f'Specify the OpenAI model to use (default: {OPENAI_MODEL}).'
    )
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help='Enable debug logging.'
    )
    parser.add_argument(
        'query',
        nargs='*',
        help='The natural language query. Reads from stdin if omitted.'
    )
    args = parser.parse_args()

    # --- Configure Logging Level ---
    if args.debug:
        log.setLevel(logging.DEBUG)
        log.debug("Debug logging enabled.")
    # If not debug, logger retains the WARNING level set by basicConfig
    # else:
    #    log.setLevel(logging.INFO) # REMOVED THIS LINE

    log.debug(f"Parsed arguments: {args}")

    if not OPENAI_API_KEY:
        log.critical("OPENAI_API_KEY environment variable not set.")
        console.print("[bold red]Error:[/bold red] OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    user_query = ""
    if args.query:
        user_query = " ".join(args.query)
        log.debug("Query read from arguments.")
    elif not sys.stdin.isatty():
        user_query = sys.stdin.read().strip()
        log.debug("Query read from stdin.")
    else:
        parser.print_help()
        log.error("No query provided via arguments or stdin in interactive mode.")
        console.print("\n[bold red]Error:[/bold red] No query provided via arguments or stdin.")
        sys.exit(1)

    if not user_query:
        log.error("Query is empty.")
        console.print("[bold red]Error:[/bold red] Query cannot be empty.")
        sys.exit(1)

    # Use INFO for the main processing step
    log.info(f"Processing query: \"{user_query}\"")
    console.print(f"Query: \"{user_query}\"")

    context = collect_context()
    selected_history = []

    if sys.stdin.isatty() and sys.stdout.isatty():
        log.debug("Running interactively, prompting for history selection.")
        selected_history = prompt_for_history_selection(context.get('potential_history', []))
    else:
         log.info("Not running interactively, skipping history selection.")

    final_context_for_api = context.copy()
    del final_context_for_api['potential_history']
    if selected_history:
        final_context_for_api['selected_history'] = selected_history
        log.debug(f"Final context includes {len(selected_history)} selected history lines.")
    else:
         log.debug("Final context includes no history.")

    suggested_command = get_command_from_openai(final_context_for_api, user_query, args.model)

    if not suggested_command:
        sys.exit(1)

    if args.execute:
        log.debug("Execute flag is set, proceeding to confirmation.")
        run_command_with_confirmation(suggested_command, user_query)
    else:
        log.debug("Execute flag not set, printing command only.")
        console.print("--- Suggested Command ---", style="bold cyan")
        console.print(suggested_command, style="bold")
        console.print("---", style="bold cyan")
        console.print("Run with -x or --execute to enable execution.", style="dim")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
         log.info("Operation cancelled by user (KeyboardInterrupt).")
         console.print("\nOperation cancelled by user.", style="yellow")
         sys.exit(1)
    except Exception as e:
         log.exception("An unhandled error occurred in main.")
         console.print(f"\n[bold red]An unexpected error occurred:[/bold red] {e}")
         sys.exit(1)