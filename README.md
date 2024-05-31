# VS code extension for voyager code-completions

## Introduction
Voyager is a INFINEON specific tool for data extraction and this extension is to help code completion for pydantic python module during plugin development.

## 3 simple steps to get this extension working
- Step1: Search and Install the `voyager-codecompletion-extension` (Voyager code completion) extension in your VS code
- Step2: Default pydantic model file is auto picked from ~/.voyager_current_model/model.py. Users can also set the parameter `voyager-codecompletion-extension.args` to value like ['path of pydantic model.py'] (i.e. a list containing single model.py path) in their vscode settings.ts or the settings of this extension to reflect the path to their custom pydantic model files to be used for code completion. 
- Step3: You are now ready, Just open your Voyager plugin code python module file, and now anywhere you type  `self.pydantic_module` along with Ctrl+Space button press provides the necessary code completions. 

