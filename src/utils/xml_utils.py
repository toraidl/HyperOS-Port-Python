import os
import re
import logging
from pathlib import Path

class XmlUtils:
    def __init__(self):
        self.logger = logging.getLogger("XmlUtils")

    def get_res_dir(self, work_dir: Path) -> Path:
        """
        Smartly retrieve real res directory.
        Compatible with APKEditor (resources/package_*/res/) and Apktool (res/).
        """
        possible_res_dirs = []

        # 1. Collect standard res directory from Apktool or normal APK
        standard_res = work_dir / "res"
        if standard_res.exists():
            possible_res_dirs.append(standard_res)

        # 2. Collect res directory from APKEditor multi-package structure
        resources_dir = work_dir / "resources"
        if resources_dir.exists() and resources_dir.is_dir():
            for pkg_dir in resources_dir.glob("package_*"):
                pkg_res = pkg_dir / "res"
                if pkg_res.exists():
                    possible_res_dirs.append(pkg_res)

        # 3. Ultimate radar: Search for directory containing values/strings.xml among all candidates
        for res in possible_res_dirs:
            if (res / "values" / "strings.xml").exists() or (res / "values" / "arrays.xml").exists():
                self.logger.debug(f"Targeting resource directory: {res.relative_to(work_dir)}")
                return res

        # 4. Fallback: If not found (e.g. minimal APP with no strings), return the first found or standard res/
        if possible_res_dirs:
            return possible_res_dirs[0]
            
        return work_dir / "res"

    def get_res_dir_old(self, root_dir: Path) -> Path | None:
        """
        Find resource root directory (res) under unpacked directory
        Compatible with APKEditor (root/res) and Apktool (possibly root/res)
        """
        # 1. Check root/res directly
        res = root_dir / "res"
        if res.exists():
            return res
        
        # 2. Deep search (prevent strange structure like package_1/res from APKEditor)
        # Find directory containing strings.xml and go up
        try:
            val = next(root_dir.rglob("strings.xml"))
            # strings.xml is in .../res/values/strings.xml
            return val.parent.parent
        except StopIteration:
            return None

    def get_id(self, res_dir: Path, name: str) -> str | None:
        """Get resource ID from public.xml"""
        if not res_dir: return None
        public_xml = res_dir / "values/public.xml"
        if not public_xml.exists(): return None

        content = public_xml.read_text(encoding='utf-8', errors='ignore')
        # Match <public ... name="name" id="0x..." />
        match = re.search(f'name="{name}" id="(0x[0-9a-f]+)"', content)
        return match.group(1) if match else None
    
    def add_string(self, res_dir: Path, name: str, value: str, lang_suffix: str = ""):
        """
        Inject string into strings.xml and automatically register valid resource ID in public.xml
        :param lang_suffix: Language suffix, e.g. "zh-rCN" (corresponds to values-zh-rCN), empty for default values
        """
        if not res_dir: return
        
        # =========================================================
        # Auto-register Public ID to prevent APKEditor errors!
        # Regardless of language injection, ensure the string has a valid ID in public.xml first
        # add_public_id has built-in deduplication
        # =========================================================
        self.add_public_id(res_dir, "string", name)

        target_dir = None
        
        # [Core Fix Area] Isolate fuzzy matching to prevent cross-directory hijacking
        if not lang_suffix:
            # 1. Inject English (default language), must exactly match "values" folder
            exact_dir = res_dir / "values"
            if exact_dir.exists() and exact_dir.is_dir():
                target_dir = exact_dir
        else:
            # 2. Inject language with suffix, use strict prefix matching for compatibility (e.g. -v26)
            dir_name = f"values-{lang_suffix}"
            for d in res_dir.iterdir():
                # Must be exactly equal, or start with "values-zh-rCN-", to prevent random matching
                if d.is_dir() and (d.name == dir_name or d.name.startswith(f"{dir_name}-")):
                    target_dir = d
                    break
        
        # [Fallback Logic]
        if not target_dir:
            if not lang_suffix: 
                target_dir = res_dir / "values" 
                target_dir.mkdir(parents=True, exist_ok=True) 
            else: 
                return 


        target_file = target_dir / "strings.xml"
        
        if not target_file.exists():
            self.logger.info(f"File {target_file.name} not found in {target_dir.name}, creating a new one.")
            empty_xml = '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n</resources>\n'
            target_file.write_text(empty_xml, encoding='utf-8', newline='\n')

        content = target_file.read_text(encoding='utf-8', errors='ignore')
        
        if f'name="{name}"' in content:
            self.logger.warning(f"String '{name}' already exists in {target_dir.name}/{target_file.name}, skipping.") 
            return

        new_line = f'\n    <string name="{name}">{value}</string>\n'
        
        parts = content.rsplit('</resources>', 1)
        
        if len(parts) == 2:
            new_content = parts[0] + new_line + '</resources>\n'
            target_file.write_text(new_content, encoding='utf-8', newline='\n')
            self.logger.debug(f"Injected string '{name}' into {target_dir.name}")
        else:
            self.logger.error(f"Failed to find </resources> tag in {target_file.name}") 


    def add_string_oof(self, res_dir: Path, name: str, value: str, lang_suffix: str = ""):
        """
        向 strings.xml 注入字符串
        :param lang_suffix: 语言后缀，如 "zh-rCN" (对应 values-zh-rCN)，留空则为默认 values
        """
        if not res_dir: return
        
        target_dir = None
        
        # =========================================================
        # [核心修复区] 彻底隔离模糊匹配，防止跨目录劫持
        # =========================================================
        if not lang_suffix:
            # 1. 如果是注入英文 (默认语言)，必须精确匹配 "values" 文件夹
            exact_dir = res_dir / "values"
            if exact_dir.exists() and exact_dir.is_dir():
                target_dir = exact_dir
        else:
            # 2. 如果是注入中文等带后缀语言，为了兼容 -v26 等，使用严格的前缀匹配
            dir_name = f"values-{lang_suffix}"
            for d in res_dir.iterdir():
                # 必须完全等于，或者以 "values-zh-rCN-" 开头，杜绝乱匹配
                if d.is_dir() and (d.name == dir_name or d.name.startswith(f"{dir_name}-")):
                    target_dir = d
                    break
        
        # =========================================================
        # [造壳与兜底逻辑]
        # =========================================================
        if not target_dir:
            if not lang_suffix: 
                target_dir = res_dir / "values" 
                target_dir.mkdir(parents=True, exist_ok=True) 
            else: 
                return 

        target_file = target_dir / "strings.xml"
        
        if not target_file.exists():
            self.logger.info(f"File {target_file.name} not found in {target_dir.name}, creating a new one.")
            empty_xml = '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n</resources>\n'
            target_file.write_text(empty_xml, encoding='utf-8', newline='\n')

        content = target_file.read_text(encoding='utf-8', errors='ignore')
        
        if f'name="{name}"' in content:
            self.logger.warning(f"String '{name}' already exists in {target_dir.name}/{target_file.name}, skipping.") 
            return

        new_line = f'\n    <string name="{name}">{value}</string>\n'
        
        parts = content.rsplit('</resources>', 1)
        
        if len(parts) == 2:
            new_content = parts[0] + new_line + '</resources>\n'
            target_file.write_text(new_content, encoding='utf-8', newline='\n')
            self.logger.debug(f"Injected string '{name}' into {target_dir.name}")
        else:
            self.logger.error(f"Failed to find </resources> tag in {target_file.name}") 

    def add_string_old(self, res_dir: Path, name: str, value: str, lang_suffix: str = ""):
        """
        向 strings.xml 注入字符串
        :param lang_suffix: 语言后缀，如 "zh-rCN" (对应 values-zh-rCN)，留空则为默认 values
        """
        if not res_dir: return
        
        dir_name = "values" + (f"-{lang_suffix}" if lang_suffix else "")
        target_dir = None
        
        # 模糊匹配目录 (处理 values-zh-rCN-v26 这种情况)
        for d in res_dir.iterdir():
            if d.is_dir() and d.name.startswith(dir_name):
                target_dir = d
                break
        
        # 如果指定了语言但没找到对应目录，通常选择跳过 (或者可以强制创建，视需求而定)
        if not target_dir:
            if not lang_suffix: 
                target_dir = res_dir / "values" # 默认目录必须有
            else: 
                return 

        target_file = target_dir / "strings.xml"
        if not target_file.exists(): return

        content = target_file.read_text(encoding='utf-8', errors='ignore')
        
        # 检查是否已存在
        if f'name="{name}"' in content:
            self.logger.warning(f"String '{name}' already exists in {target_file.name}, skipping.") # <--- 加这行 
            return

        # 在 </resources> 标签前插入
        # 使用 replace 替换闭合标签
       # new_line = f'    <string name="{name}">{value}</string>'
       # new_content = content.replace('</resources>', f'{new_line}\n</resources>')
        new_line = f'\n    <string name="{name}">{value}</string>\n'
        
        # 使用 rsplit 只切分最后一次出现的 </resources>
        parts = content.rsplit('</resources>', 1)
        
        if len(parts) == 2:
            # 确保 </resources> 前面有一个纯净的换行符，并且文件末尾也有一个换行符
            new_content = parts[0] + new_line + '</resources>\n'
            
            # [关键点] 写入时强制使用 Unix 风格的换行符 (LF)，避免 Windows 的 \r\n 干扰 APKEditor
            target_file.write_text(new_content, encoding='utf-8', newline='\n')
            self.logger.debug(f"Injected string '{name}' into {target_dir.name}")
        else:
            self.logger.error(f"Failed to find </resources> tag in {target_file.name}") 
        #target_file.write_text(new_content, encoding='utf-8')
        #self.logger.debug(f"Injected string '{name}' into {target_dir.name}")

    def add_public_id(self, res_dir: Path, res_type: str, name: str) -> str | None:
        """
        向 public.xml 注册新 ID (自动增长，无视属性顺序)
        """
        import re
        if not res_dir: return None
        public_xml = res_dir / "values/public.xml"
        if not public_xml.exists(): return None

        content = public_xml.read_text(encoding='utf-8', errors='ignore')
        
        # 1. 检查是否已存在 (宽泛匹配)
        if f'name="{name}"' in content:
            # 不限制属性顺序，只要在同一个 <public> 标签内包含 name 和 id 即可
            match = re.search(rf'<public[^>]*name="{name}"[^>]*id="(0x[0-9a-fA-F]+)"', content)
            if match:
                return match.group(1)

        # 2. 计算新 ID (找到同类型的最大 ID)
        ids = []
        # 匹配所有的 <public ...> 标签内容
        for match in re.finditer(r'<public([^>]+)>', content):
            attrs = match.group(1)
            # 检查这个标签是否属于我们要找的资源类型
            if f'type="{res_type}"' in attrs:
                # 提取 ID (无视它在 type 的前面还是后面)
                id_match = re.search(r'id="(0x[0-9a-fA-F]+)"', attrs)
                if id_match:
                    ids.append(int(id_match.group(1), 16))
        
        if not ids:
            if res_type == "string": new_id_int = 0x7f100000
            elif res_type == "id": new_id_int = 0x7f0b0000
            else: new_id_int = 0x7f010000 
        else:
            new_id_int = max(ids) + 1
        
        new_id_hex = f"0x{new_id_int:x}"
        
        # 3. 插入新 ID 并使用规范的换行符
        line = f'\n    <public type="{res_type}" name="{name}" id="{new_id_hex}" />\n'
        
        parts = content.rsplit('</resources>', 1)
        if len(parts) == 2:
            new_content = parts[0] + line + '</resources>\n'
            public_xml.write_text(new_content, encoding='utf-8', newline='\n')
        else:
            # 兜底
            new_content = content.replace('</resources>', f'{line}</resources>')
            public_xml.write_text(new_content, encoding='utf-8')
        
        self.logger.info(f"Generated Public ID for {name}: {new_id_hex}")
        return new_id_hex

    def add_public_id_ooj(self, res_dir: Path, res_type: str, name: str) -> str | None:
        """
        向 public.xml 注册新 ID (自动增长)
        :param res_type: 资源类型 (string, layout, id, array 等)
        :return: 生成的十六进制 ID (字符串)
        """
        if not res_dir: return None
        public_xml = res_dir / "values/public.xml"
        if not public_xml.exists(): return None

        content = public_xml.read_text(encoding='utf-8', errors='ignore')
        
        # 1. 检查是否已存在
        if f'name="{name}"' in content:
            match = re.search(f'type="{res_type}" name="{name}" id="(0x[0-9a-f]+)"', content)
            if match:
                return match.group(1)

        # 2. 计算新 ID
        # 找到同类型的最大 ID
        ids = []
        # 正则匹配该类型的所有 ID
        pattern = re.compile(f'type="{res_type}".*?id="(0x[0-9a-f]+)"')
        for m in pattern.finditer(content):
            ids.append(int(m.group(1), 16))
        
        if not ids:
            # 如果该类型一个ID都没有，给个默认起始值 (视 APK 而定，通常是 0x7fXXXXXX)
            # 这是一个兜底策略
            if res_type == "string": new_id_int = 0x7f100000
            elif res_type == "id": new_id_int = 0x7f0b0000
            else: new_id_int = 0x7f010000 
        else:
            new_id_int = max(ids) + 1
        
        new_id_hex = f"0x{new_id_int:x}"
        
        # 3. 插入
        line = f'    <public type="{res_type}" name="{name}" id="{new_id_hex}" />'
        new_content = content.replace('</resources>', f'{line}\n</resources>')
        public_xml.write_text(new_content, encoding='utf-8')
        
        self.logger.info(f"Generated Public ID for {name}: {new_id_hex}")
        return new_id_hex

    def add_array_item_old(self, res_dir: Path, array_type: str, array_name: str, value: str):
        """
        向 arrays.xml 或 strings.xml 中的数组追加 item
        :param array_type: array, string-array, integer-array
        :param array_name: 数组的 name 属性
        :param value: 要插入的值 (如 @string/xxx 或 5)
        """
        if not res_dir: return
        
        # 数组定义通常在 arrays.xml，但也可能在 strings.xml
        candidate_files = ["arrays.xml", "strings.xml"]
        
        for fname in candidate_files:
            fpath = res_dir / "values" / fname
            if not fpath.exists(): continue

            content = fpath.read_text(encoding='utf-8', errors='ignore')
            
            # 定位数组开始标签: <string-array name="xxx">
            # 简单的字符串查找
            tag_start = f'name="{array_name}"'
            
            if tag_start in content:
                # 使用正则提取整个数组块
                # <string-array name="xxx"> ... </string-array>
                # 注意处理 type (array 可能是 <array> 或 <string-array>)
                
                # 构造正则来匹配闭合标签
                # 闭合标签可能是 </string-array>, </integer-array>, </array>
                # 我们假设标签名与开启标签对应，或者直接匹配 </.*array>
                
                pattern = re.compile(f'({tag_start}.*?)(</.*?array>)', re.DOTALL)
                match = pattern.search(content)
                
                if match:
                    full_block = match.group(0)
                    closing_tag = match.group(2) # e.g. </string-array>
                    
                    # 检查是否已经存在该 item (防止重复添加)
                    if f'<item>{value}</item>' in full_block:
                        return

                    # 插入 item
                    item_line = f'        <item>{value}</item>'
                    new_block = full_block.replace(closing_tag, f'{item_line}\n    {closing_tag}')
                    
                    new_content = content.replace(full_block, new_block)
                    fpath.write_text(new_content, encoding='utf-8')
                    self.logger.debug(f"Added item '{value}' to array '{array_name}' in {fname}")
                    return # 找到并修改后直接返回

    def add_array_item(self, res_dir: Path, array_name: str, items: list, array_type: str = None, lang_suffix: str = ""):
        """
        向 arrays.xml 中的指定数组批量追加多个 <item>
        """
        import re
        if not res_dir or not items: 
            return

        target_dir = None

        # =========================================================
        # [核心修复区] 彻底隔离模糊匹配，防止跨目录劫持 (与 add_string 保持一致)
        # =========================================================
        if not lang_suffix:
            # 1. 注入默认语言，必须精确匹配 "values" 文件夹
            exact_dir = res_dir / "values"
            if exact_dir.exists() and exact_dir.is_dir():
                target_dir = exact_dir
        else:
            # 2. 注入带后缀语言，使用严格的前缀匹配
            dir_name = f"values-{lang_suffix}"
            for d in res_dir.iterdir():
                if d.is_dir() and (d.name == dir_name or d.name.startswith(f"{dir_name}-")):
                    target_dir = d
                    break

        if not target_dir:
            if not lang_suffix:
                target_dir = res_dir / "values"
            else:
                return

        target_file = target_dir / "arrays.xml"
        if not target_file.exists():
            target_file = target_dir / "strings.xml"
            if not target_file.exists():
                return

        content = target_file.read_text(encoding='utf-8', errors='ignore')

        # 1. 匹配整个数组块
        # 修复了你终端复制造成的代码截断问题
        pattern = re.compile(
            rf'(<(?P<tag>string-array|integer-array|array)\s+name="{array_name}"[^>]*>)(.*?)(</(?P=tag)>)', 
            re.DOTALL
        )
        
        match = pattern.search(content)
        if not match:
            self.logger.warning(f"Array '{array_name}' not found in {target_dir.name}/{target_file.name}")
            return
            
        open_tag = match.group(1)      
        inner_content = match.group(3) 
        close_tag = match.group(4)     
        
        # 2. 遍历列表，防重注入
        added_count = 0
        new_inner = inner_content
        
        for item in items:
            if f'>{item}</item>' not in new_inner:
                new_inner += f'\n        <item>{item}</item>'
                added_count += 1
                
        if added_count == 0:
            return
            
        # 3. 缝合
        if not new_inner.endswith('\n'):
            new_inner += '\n    '
        else:
            new_inner += '    '
            
        new_block = f"{open_tag}{new_inner}{close_tag}"
        new_content = content[:match.start()] + new_block + content[match.end():]
        
        # 4. 安全写入
        target_file.write_text(new_content, encoding='utf-8', newline='\n')
        self.logger.debug(f"Injected {added_count} items into array '{array_name}' ({target_dir.name}/{target_file.name})")

    def add_array_item_idd(self, res_dir: Path, array_name: str, items: list, array_type: str = None, lang_suffix: str = ""):
        """
        向 arrays.xml 中的指定数组批量追加多个 <item>
        
        :param res_dir: res 资源目录的 Path 对象
        :param array_name: 数组的 name 属性
        :param items: 需要插入的 item 列表 (如 ["5", "7"])
        :param array_type: 兼容参数 (如 "string-array")，实际内部会自动正则推断
        :param lang_suffix: 语言后缀
        """
        import re
        if not res_dir or not items: 
            return

        dir_name = "values" + (f"-{lang_suffix}" if lang_suffix else "")
        target_dir = None

        for d in res_dir.iterdir():
            if d.is_dir() and d.name.startswith(dir_name):
                target_dir = d
                break

        if not target_dir:
            if not lang_suffix:
                target_dir = res_dir / "values"
            else:
                return

        target_file = target_dir / "arrays.xml"
        if not target_file.exists():
            target_file = target_dir / "strings.xml"
            if not target_file.exists():
                return

        content = target_file.read_text(encoding='utf-8', errors='ignore')

        # 1. 匹配整个数组块
        # (?P<tag>...) 兼容 string-array, integer-array 等
        pattern = re.compile(
            rf'(<(?P<tag>string-array|integer-array|array)\s+name="{array_name}"[^>]*>)(.*?)(</(?P=tag)>)', 
            re.DOTALL
        )
        
        match = pattern.search(content)
        if not match:
            self.logger.warning(f"Array '{array_name}' not found in {target_file.name}")
            return
            
        open_tag = match.group(1)      
        inner_content = match.group(3) 
        close_tag = match.group(4)     
        
        # 2. 遍历列表，防重注入
        added_count = 0
        new_inner = inner_content
        
        for item in items:
            # 如果这个 item 还没被加进去过
            if f'>{item}</item>' not in new_inner:
                # 保持标准的 8 空格缩进
                new_inner += f'\n        <item>{item}</item>'
                added_count += 1
                
        # 如果所有元素都已经存在了，直接结束，不写入文件
        if added_count == 0:
            return
            
        # 3. 缝合末尾的换行符和缩进，对齐闭合标签
        if not new_inner.endswith('\n'):
            new_inner += '\n    '
        else:
            new_inner += '    '
            
        new_block = f"{open_tag}{new_inner}{close_tag}"
        new_content = content[:match.start()] + new_block + content[match.end():]
        
        # 4. 安全写入
        target_file.write_text(new_content, encoding='utf-8', newline='\n')
        self.logger.debug(f"Injected {added_count} items into array '{array_name}' ({target_file.name})")
 
