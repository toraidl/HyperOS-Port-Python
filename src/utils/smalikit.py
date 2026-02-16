#!/usr/bin/env python3
import os
import sys
import re
import argparse

class SmaliArgs:
    def __init__(self, **kwargs):
        # Set default values for all arguments (corresponds to dest in argparse)
        self.path = None
        self.file_path = None
        self.method = None
        self.seek_keyword = None
        self.iname = None
        self.remake = None
        self.replace_in_method = None
        self.regex_replace = None
        self.delete_in_method = None
        self.delete_method = False
        self.after_line = None
        self.before_line = None
        self.insert_line = None
        self.recursive = False
        self.return_type = None
        
        # Override default values with passed keyword arguments
        self.__dict__.update(kwargs)
        
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

class SmaliKit:
    def __init__(self, args):
        self.args = args
        self.target_method = args.method
        self.seek_keyword = args.seek_keyword
        
        # 1. If -m is specified, method name match must be satisfied first
        if self.target_method:
            # Smart detection: if user inputs brackets (e.g. full signature), do not automatically add r"\s*\("
            if "(" in self.target_method:
                # Allow user to pass full signature: getActivity(Landroid/content/Context;...)
                method_name_pattern = re.escape(self.target_method)
            else:
                # User only passed name: getActivity -> automatically match getActivity(
                method_name_pattern = re.escape(self.target_method) + r"\s*\("
        
        # 2. If -m is not specified but -seek is, allow matching all method names
        elif self.seek_keyword:
            method_name_pattern = r".*?" 
            
        else:
            print(f"{Colors.FAIL}[ERROR] You must provide either -m (Method Name) or -seek (Keyword search){Colors.ENDC}")
            sys.exit(1)

        # Compile regex
        self.method_pattern = re.compile(
            r"(?P<header>^\s*\.method[^\n\r]*?\s%s[^\n\r]*)"
            r"(?P<body>.*?)"
            r"(?P<footer>^\s*\.end method)"
            % method_name_pattern,
            re.DOTALL | re.MULTILINE
        )


    def log(self, message, color=Colors.ENDC):
        print(f"{color}{message}{Colors.ENDC}")

    def apply_modifications(self, original_body):
        """
        Core modification logic: Input original Body, return (new Body, is_modified)
        """
        new_body = original_body
        is_modified = False

        # 1. -remake (Rewrite entire method body)
        if self.args.remake:
            remake_content = self.args.remake.replace('\\n', '\n')
            new_body = f"\n    {remake_content}\n"
            is_modified = True

        # 2. -rim (String replacement)
        if self.args.replace_in_method:
            old_str, new_str = self.args.replace_in_method
            if old_str in new_body:
                new_body = new_body.replace(old_str, new_str)
                is_modified = True

        # 3. -reg (Regex replacement)
        if self.args.regex_replace:
            pattern_str, repl_str = self.args.regex_replace
            pattern = re.compile(pattern_str)
            if pattern.search(new_body):
                new_body = pattern.sub(repl_str, new_body)
                is_modified = True

        # 4. -dim (Delete string)
        if self.args.delete_in_method:
            target_str = self.args.delete_in_method
            if target_str in new_body:
                new_body = new_body.replace(target_str, "")
                is_modified = True
        
        # 5. -al (Insert line AFTER match)
        if self.args.after_line:
            target_line, add_line = self.args.after_line
            if target_line in new_body:
                new_body = new_body.replace(target_line, f"{target_line}\n    {add_line}")
                is_modified = True

        # 6. -bl (Insert line BEFORE match)
        if self.args.before_line:
            target_line, add_line = self.args.before_line
            if target_line in new_body:
                new_body = new_body.replace(target_line, f"    {add_line}\n{target_line}")
                is_modified = True

        # 7. -il (Insert at specific line)
        if self.args.insert_line:
            line_idx_str, insert_code = self.args.insert_line
            try:
                line_idx = int(line_idx_str)
                # Split Body by lines
                lines = new_body.split('\n')
                
                # Handle format of inserted code (handle \n and add indentation)
                code_lines = insert_code.replace('\\n', '\n').split('\n')
                formatted_lines = [f"    {line.strip()}" for line in code_lines]
                block_to_insert = "\n".join(formatted_lines)
                
                # Boundary check and insertion
                # Note: lines[0] is usually empty string (because regex captured body starts with newline)
                # So lines[1] is the actual first line of code (.locals)
                
                if line_idx < 0: line_idx = 0
                if line_idx > len(lines): line_idx = len(lines)
                
                lines.insert(line_idx, block_to_insert)
                
                new_body = "\n".join(lines)
                is_modified = True
            except ValueError:
                self.log(f"[ERROR] Invalid line number: {line_idx_str}", Colors.FAIL)

        return new_body, is_modified


    def process_content(self, content, file_path):
        matches = list(self.method_pattern.finditer(content))
        
        if not matches:
            return content, False

        file_modified = False
        replacements = []

        for match in matches:
            header = match.group('header')
            body = match.group('body')
            footer = match.group('footer')
            full_block = match.group(0)

            if self.seek_keyword and self.seek_keyword not in body:
                continue 

            if self.args.return_type:
                target_ret_sig = f"){self.args.return_type}"
                if target_ret_sig not in header:
                    continue

            method_sig = header.strip()
            self.log(f"[*] Target Found: {os.path.basename(file_path)} -> {Colors.BOLD}{method_sig}{Colors.ENDC}", Colors.OKBLUE)

            if self.args.delete_method:
                self.log(f"  -> Applying -dm (Delete Method)...", Colors.FAIL)
                replacements.append((full_block, "")) 
                file_modified = True
                continue

            new_body, body_modified = self.apply_modifications(body)

            if body_modified:
                new_block = f"{header}{new_body}{footer}"
                if full_block != new_block:
                    replacements.append((full_block, new_block))
                    file_modified = True

        new_content = content
        for old, new in replacements:
            new_content = new_content.replace(old, new, 1)

        return new_content, file_modified

    def walk_and_patch(self, start_path):
        if os.path.isfile(start_path):
            self.patch_file(start_path)
            return

        if os.path.isdir(start_path):
            for root, _, files in os.walk(start_path):
                for file in files:
                    if file.endswith(".smali"):
                        if self.args.iname and self.args.iname not in file:
                            continue
                        self.patch_file(os.path.join(root, file))
        else:
            self.log(f"[ERROR] Path not found: {start_path}", Colors.FAIL)

    def patch_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            if self.target_method and self.target_method not in content and not self.seek_keyword:
                return False

            new_content, patched = self.process_content(content, file_path)
            if patched:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                self.log(f"  -> [SUCCESS] Saved: {file_path}", Colors.OKGREEN)
                return True
        except Exception as e:
            self.log(f"[ERROR] processing {file_path}: {e}", Colors.FAIL)
        return False

def main():
    parser = argparse.ArgumentParser(description="Python smali_kit with Auto-Search & Hook")
    
    parser.add_argument('-p', dest='path', help="Path to smali folder")
    parser.add_argument('-f', dest='file_path', help="Specific file to patch (Overrides -p)")
    
    parser.add_argument('-m', dest='method', help="Exact method name to target")
    parser.add_argument('-seek', dest='seek_keyword', help="Search for a method containing this string")
    
    parser.add_argument('-in', dest='iname', help="Filter by filename (only for folder mode)")
    parser.add_argument('-remake', dest='remake', help="Replace entire method body")
    parser.add_argument('-rim', dest='replace_in_method', nargs=2, help="String Replace: OLD NEW")
    parser.add_argument('-reg', dest='regex_replace', nargs=2, metavar=('PATTERN', 'REPLACEMENT'), help="Regex Replace")
    parser.add_argument('-dim', dest='delete_in_method', help="Delete specific string")
    parser.add_argument('-dm', dest='delete_method', action='store_true', help="Delete entire method")
    parser.add_argument('-al', dest='after_line', nargs=2, help="Insert line AFTER string match")
    parser.add_argument('-bl', dest='before_line', nargs=2, help="Insert line BEFORE string match")
    # [新增参数]
    parser.add_argument('-il', dest='insert_line', nargs=2, metavar=('LINE_NUM', 'CODE'), help="Insert code at specific line number (1-based index)")
    
    parser.add_argument('-recursive', dest='recursive', action='store_true')
    parser.add_argument('-ret', dest='return_type', help="Filter by Smali return type (e.g. Z, V, I)")
    
    args, unknown = parser.parse_known_args()
    
    target_path = args.file_path if args.file_path else args.path
    
    if not target_path:
        print(f"{Colors.FAIL}[ERROR] You must provide either -p (folder) or -f (file){Colors.ENDC}")
        sys.exit(1)

    patcher = SmaliKit(args)
    patcher.walk_and_patch(target_path)

if __name__ == "__main__":
    main()
