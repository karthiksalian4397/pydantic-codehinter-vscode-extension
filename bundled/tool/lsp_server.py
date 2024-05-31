# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Implementation of tool support over LSP."""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import sys
import sysconfig
import traceback
from typing import Any, Optional, Sequence, get_type_hints
import importlib
import inspect
import typing

# **********************************************************
# Update sys.path before importing any bundled libraries.
# **********************************************************
def update_sys_path(path_to_add: str, strategy: str) -> None:
    """Add given path to `sys.path`."""
    if path_to_add not in sys.path and os.path.isdir(path_to_add):
        if strategy == "useBundled":
            sys.path.insert(0, path_to_add)
        elif strategy == "fromEnvironment":
            sys.path.append(path_to_add)


# Ensure that we can import LSP libraries, and other bundled libraries.
update_sys_path(
    os.fspath(pathlib.Path(__file__).parent.parent / "libs"),
    os.getenv("LS_IMPORT_STRATEGY", "useBundled"),
)

# **********************************************************
# Imports needed for the language server goes below this.
# **********************************************************
# pylint: disable=wrong-import-position,import-error
import lsp_jsonrpc as jsonrpc
import lsp_utils as utils
import lsprotocol.types as lsp
from pygls import server, uris, workspace
from pygls.server import LanguageServer
from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    CompletionItem,
    CompletionList,
    CompletionParams,
)

WORKSPACE_SETTINGS = {}
GLOBAL_SETTINGS = {}
RUNNER = pathlib.Path(__file__).parent / "lsp_runner.py"

MAX_WORKERS = 5

LSP_SERVER = server.LanguageServer(
    name="Voyager code completion", version="1.0.0", max_workers=MAX_WORKERS
)



# **********************************************************
# Required Language Server Initialization and Exit handlers.
# **********************************************************
@LSP_SERVER.feature(lsp.INITIALIZE)
def initialize(params: lsp.InitializeParams) -> None:
    """LSP handler for initialize request."""
    log_to_output(f"CWD Server: {os.getcwd()}")

    paths = "\r\n   ".join(sys.path)
    log_to_output(f"sys.path used to run Server:\r\n   {paths}")

    GLOBAL_SETTINGS.update(**params.initialization_options.get("globalSettings", {}))

    settings = params.initialization_options["settings"]
    _update_workspace_settings(settings)
    log_to_output(
        f"Settings used to run Server:\r\n{json.dumps(settings, indent=4, ensure_ascii=False)}\r\n"
    )
    log_to_output(
        f"Global settings:\r\n{json.dumps(GLOBAL_SETTINGS, indent=4, ensure_ascii=False)}\r\n"
    )


@LSP_SERVER.feature(lsp.EXIT)
def on_exit(_params: Optional[Any] = None) -> None:
    """Handle clean up on exit."""
    jsonrpc.shutdown_json_rpc()


@LSP_SERVER.feature(lsp.SHUTDOWN)
def on_shutdown(_params: Optional[Any] = None) -> None:
    """Handle clean up on shutdown."""
    jsonrpc.shutdown_json_rpc()


def _get_global_defaults():
    return {
        "path": GLOBAL_SETTINGS.get("path", []),
        "interpreter": GLOBAL_SETTINGS.get("interpreter", [sys.executable]),
        "args": GLOBAL_SETTINGS.get("args", []),
        "importStrategy": GLOBAL_SETTINGS.get("importStrategy", "useBundled"),
        "showNotifications": GLOBAL_SETTINGS.get("showNotifications", "off"),
    }


def _update_workspace_settings(settings):
    if not settings:
        key = os.getcwd()
        WORKSPACE_SETTINGS[key] = {
            "cwd": key,
            "workspaceFS": key,
            "workspace": uris.from_fs_path(key),
            **_get_global_defaults(),
        }
        return

    for setting in settings:
        key = uris.to_fs_path(setting["workspace"])
        WORKSPACE_SETTINGS[key] = {
            "cwd": key,
            **setting,
            "workspaceFS": key,
        }


def _get_settings_by_path(file_path: pathlib.Path):
    workspaces = {s["workspaceFS"] for s in WORKSPACE_SETTINGS.values()}

    while file_path != file_path.parent:
        str_file_path = str(file_path)
        if str_file_path in workspaces:
            return WORKSPACE_SETTINGS[str_file_path]
        file_path = file_path.parent

    setting_values = list(WORKSPACE_SETTINGS.values())
    return setting_values[0]


def _get_document_key(document: workspace.Document):
    if WORKSPACE_SETTINGS:
        document_workspace = pathlib.Path(document.path)
        workspaces = {s["workspaceFS"] for s in WORKSPACE_SETTINGS.values()}

        # Find workspace settings for the given file.
        while document_workspace != document_workspace.parent:
            if str(document_workspace) in workspaces:
                return str(document_workspace)
            document_workspace = document_workspace.parent

    return None


def _get_settings_by_document(document: workspace.Document | None):
    if document is None or document.path is None:
        return list(WORKSPACE_SETTINGS.values())[0]

    key = _get_document_key(document)
    if key is None:
        # This is either a non-workspace file or there is no workspace.
        key = os.fspath(pathlib.Path(document.path).parent)
        return {
            "cwd": key,
            "workspaceFS": key,
            "workspace": uris.from_fs_path(key),
            **_get_global_defaults(),
        }

    return WORKSPACE_SETTINGS[str(key)]



# *****************************************************
# Logging and notification.
# *****************************************************
def log_to_output(
    message: str, msg_type: lsp.MessageType = lsp.MessageType.Log
) -> None:
    LSP_SERVER.show_message_log(message, msg_type)


def log_error(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Error)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onError", "onWarning", "always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Error)


def log_warning(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Warning)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onWarning", "always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Warning)


def log_always(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Info)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Info)


@LSP_SERVER.feature(TEXT_DOCUMENT_COMPLETION)
def completions(params: CompletionParams):
    items = []
    document = LSP_SERVER.workspace.get_text_document(params.text_document.uri)
    current_line = document.lines[params.position.line].strip()

    server_args = _get_global_defaults()["args"]
    pydantic_module_path = server_args[0] 
    model_attributes = re.compile(r"self\.pydantic_module\.([^\.]*)\.$")
    attributes_field_info = re.compile(r"self\.pydantic_module\.([^\.]*)\.([^\.]*)\.$")
    if current_line.endswith("self.pydantic_module."):
        # extract module formatted path
        pydantic_module_path = os.path.expanduser(pydantic_module_path)
        module_name = os.path.splitext(os.path.basename(pydantic_module_path))[0]
        module_dir = pydantic_module_path.replace(module_name+'.py','')

        # Add the directory to the system path temporarily
        sys.path.insert(0, module_dir)

        # Import the module dynamically
        module = importlib.import_module(module_name)

        # Get all the members of the module
        module_members = inspect.getmembers(module)

        # Filter the members to get only the class names
        items = [CompletionItem(label=member[0]) for member in module_members if inspect.isclass(member[1])]
    elif model_attributes.match(current_line):
        pydantic_module_path = os.path.expanduser(pydantic_module_path)
        module_name = os.path.splitext(os.path.basename(pydantic_module_path))[0]
        module_dir = pydantic_module_path.replace(module_name+'.py','')
        class_name = model_attributes.search(current_line).group(1)
        # Add the directory to the system path temporarily
        sys.path.insert(0, module_dir)

        # Import the module dynamically
        module = importlib.import_module(module_name)

        try:
            # Get the class dynamically
            class_object = getattr(module, class_name)
            
            # Get type hints and print the attributes
            items = [CompletionItem(label=item) for item in get_type_hints(class_object).keys()]  

        except AttributeError:
            items = []

   
    elif attributes_field_info.match(current_line):
        pydantic_module_path = os.path.expanduser(pydantic_module_path)
        module_name = os.path.splitext(os.path.basename(pydantic_module_path))[0]
        module_dir = pydantic_module_path.replace(module_name+'.py','')
        class_name = attributes_field_info.search(current_line).group(1)
        attribute_name = attributes_field_info.search(current_line).group(2)

        # Add the directory to the system path temporarily
        sys.path.insert(0, module_dir)

        # Import the module dynamically
        module = importlib.import_module(module_name)

        try:
            # Get the class dynamically
            class_object = getattr(module, class_name)
            # Get type hints and print the attributes
            items = [CompletionItem(label='attribute type : '+  str(get_annotated_class_from_model(get_type_hints(class_object)[attribute_name])))]
        except AttributeError:
            items = [CompletionItem(label=f'No such class exist {class_name}')]
        except KeyError:
            items = [CompletionItem(label=f'No such attribute exist for {class_name}')]
    return CompletionList(
        is_incomplete=False,
        items=items,
    )

def get_annotated_class_from_model(annotation):
    """Gets the class type of attributes from the Field info annotation for a pydantic model class
    Args:
        annotation: annotation from the FieldInfo
    """
    if(isinstance(annotation, typing._GenericAlias)):
        return get_annotated_class_from_model((typing.get_args(annotation))[0])
    else:
        return annotation  

# *****************************************************
# Start the server.
# *****************************************************
if __name__ == "__main__":
    LSP_SERVER.start_io()
