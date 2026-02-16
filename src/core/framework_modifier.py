import os
import re
import shutil
import zipfile
import logging
from pathlib import Path
from src.utils.shell import ShellRunner

# [关键] 导入 SmaliKit
from src.utils.smalikit import SmaliKit

# 定义一个辅助类，用来模拟 argparse 的参数对象
class SmaliArgs:
    def __init__(self, **kwargs):
        # 设置默认值
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
        
        # 更新传入的参数
        self.__dict__.update(kwargs)

class FrameworkModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("FrameworkModifier")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()
        
        # 工具路径
        self.apktool_path = self.bin_dir / "apktool" / "apktool"
        self.apkeditor_path = self.bin_dir / "APKEditor.jar"
        self.baksmali_path = self.bin_dir / "baksmali.jar"
        
        self.RETRUN_TRUE = ".locals 1\n    const/4 v0, 0x1\n    return v0"
        self.RETRUN_FALSE = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        self.REMAKE_VOID = ".locals 0\n    return-void"
        self.INVOKE_TRUE = "invoke-static {}, Lcom/android/internal/util/HookHelper;->RETURN_TRUE()Z"
        self.PRELOADS_SHAREDUIDS = ".locals 1\n    invoke-static {}, Lcom/android/internal/util/HookHelper;->RETURN_TRUE()Z\n    move-result v0\n    sput-boolean v0, Lcom/android/server/pm/ReconcilePackageUtils;->ALLOW_NON_PRELOADS_SYSTEM_SHAREDUIDS:Z\n    return-void"

        self.temp_dir = self.ctx.target_dir.parent / "temp_modifier"

    def run(self):
        """执行所有修改逻辑"""
        self.logger.info("Starting System Modification...")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. 修改 miui-services.jar
        self._mod_miui_services()
        
        # 2. 修改 services.jar
        self._mod_services()
        
        # 3. 修改 framework.jar (包含 PIF Patch)
        self._mod_framework()

        self._inject_xeu_toolbox()
        # if self.temp_dir.exists():
        #     shutil.rmtree(self.temp_dir)
        self.logger.info("System Modification Completed.")

    # ==========================
    # 工具辅助方法
    # ==========================
    
    def _run_smalikit(self, **kwargs):
        """调用 SmaliKit 的便捷方法"""
        args = SmaliArgs(**kwargs)
        patcher = SmaliKit(args)
        # SmaliKit 需要传入目录或文件路径
        target = args.file_path if args.file_path else args.path
        if target:
            patcher.walk_and_patch(target)

    def _apkeditor_decode(self, jar_path, out_dir):
        """使用 APKEditor 解包 JAR"""
        # java -jar bin/apktool/APKEditor.jar d -f -i $jar -o $out
        self.shell.run_java_jar(self.apkeditor_path, ["d", "-f", "-i", str(jar_path), "-o", str(out_dir)])

    def _apkeditor_build(self, src_dir, out_jar):
        """使用 APKEditor 打包 JAR"""
        self.shell.run_java_jar(self.apkeditor_path, ["b", "-f", "-i", str(src_dir), "-o", str(out_jar)])

    def _find_file(self, root, name_pattern):
        """支持通配符的文件查找"""
        for p in Path(root).rglob(name_pattern):
            if p.is_file(): return p
        return None

    def _replace_text_in_file(self, file_path, old, new):
        """简单的文本替换"""
        if not file_path or not file_path.exists():
            return
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        if old in content:
            new_content = content.replace(old, new)
            file_path.write_text(new_content, encoding='utf-8')
            self.logger.info(f"Patched {file_path.name}: {old[:20]}... -> {new[:20]}...")

    # ==========================
    # 业务逻辑方法
    # ==========================

    def _mod_miui_services(self):
        jar_path = self._find_file(self.ctx.target_dir, "miui-services.jar")
        if not jar_path: return

        self.logger.info(f"Modifying {jar_path.name}...")
        work_dir = self.temp_dir / "miui-services"
        self._apkeditor_decode(jar_path, work_dir)

        # 1. EU ROM 特殊 Patch (SystemServerImpl)
        # 假设 context 中有 is_port_eu_rom 标志
        if getattr(self.ctx, "is_port_eu_rom", False):
            fuc_body = ".locals 1\n    invoke-direct {p0}, Lcom/android/server/SystemServerStub;-><init>()V\n    return-void"
            self._run_smalikit(
                path=str(work_dir),
                iname="SystemServerImpl.smali",
                method="<init>()V",
                remake=fuc_body
            )

        # 2. PackageManagerServiceImpl 清理
        remake_void = ".locals 0\n    return-void"
        remake_false = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceImpl.smali", method="verifyIsolationViolation", remake=remake_void, recursive=True)
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceImpl.smali", method="canBeUpdate", remake=remake_void, recursive=True)
        
        # 3. 国际版标志位 Patch (IS_INTERNATIONAL_BUILD)
        # 定义需要替换的文件和规则
        patches = [
            ("com/android/server/am/BroadcastQueueModernStubImpl.smali", [
                ('sget-boolean v2, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v2, 0x1')
            ]),
            ("com/android/server/am/ActivityManagerServiceImpl.smali", [
                ('sget-boolean v1, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v1, 0x1'),
                ('sget-boolean v4, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v4, 0x1')
            ]),
            ("com/android/server/am/ProcessManagerService.smali", [
                ('sget-boolean v0, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v0, 0x1')
            ]),
            ("com/android/server/am/ProcessSceneCleaner.smali", [
                ('sget-boolean v4, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v0, 0x1')
            ]),
        ]

        for rel_path, rules in patches:
            # 在 work_dir 中查找该文件 (因为路径可能包含 smali_classes2 等)
            target_smali = self._find_file(work_dir, Path(rel_path).name)
            if target_smali:
                for old_str, new_str in rules:
                    self._replace_text_in_file(target_smali, old_str, new_str)

        # 4. WindowManagerServiceImpl Patch
        self._run_smalikit(path=str(work_dir), iname="WindowManagerServiceImpl.smali", method="notAllowCaptureDisplay(Lcom/android/server/wm/RootWindowContainer;I)Z", remake=remake_false, recursive=True)

        self._apkeditor_build(work_dir, jar_path)

    def _mod_services(self):
        jar_path = self._find_file(self.ctx.target_dir, "services.jar")
        if not jar_path: return

        self.logger.info(f"Modifying {jar_path.name}...")
        work_dir = self.temp_dir / "services"
        # 备份
        shutil.copy2(jar_path, self.temp_dir / "services.jar.bak")
        self._apkeditor_decode(jar_path, work_dir)

        # 常用 Remake Body
        remake_void = ".locals 0\n    return-void"
        remake_false = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        remake_true = ".locals 1\n    const/4 v0, 0x1\n    return v0"
        
        # PackageManagerServiceUtils
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceUtils.smali", method="checkDowngrade", remake=remake_void, recursive=True)
        for m in ["matchSignaturesCompat", "matchSignaturesRecover", "matchSignatureInSystem", "verifySignatures"]:
            self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceUtils.smali", method=m, remake=remake_false)

        # KeySetManagerService
        self._run_smalikit(path=str(work_dir), iname="KeySetManagerService.smali", method="checkUpgradeKeySetLocked", remake=remake_true)
        
        # VerifyingSession
        self._run_smalikit(path=str(work_dir), iname="VerifyingSession.smali", method="isVerificationEnabled", remake=remake_false)
        
        # ReconcilePackageUtils (预加载共享UID)
        # 注意：这里需要具体的 smali 代码，假设变量 PRELOADS_SHAREDUIDS 已定义或直接写入
        # self._run_smalikit(..., remake=PRELOADS_SHAREDUIDS)

        self._apkeditor_build(work_dir, jar_path)

    def _find_file_recursive(self, root_dir: Path, filename: str) -> Path | None:
        """辅助方法: 递归查找文件"""
        if not root_dir.exists(): return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None

    def _find_dir_recursive(self, root_dir: Path, dirname: str) -> Path | None:
        """辅助方法: 递归查找文件夹"""
        if not root_dir.exists(): return None
        # rglob 匹配的是路径末尾，需要自行过滤是否为目录
        for p in root_dir.rglob(dirname):
            if p.is_dir() and p.name == dirname:
                return p
        return None

    def _mod_framework(self):
        jar = self._find_file_recursive(self.ctx.target_dir, "framework.jar")
        if not jar: return
        self.logger.info(f"Modifying {jar.name} (PropsHook, PIF & SignBypass)...")
        
        wd = self.temp_dir / "framework"
        # [关键] 必须加 -no-dex-debug，否则部分 Smali 可能会因为调试信息导致重新打包失败或 Hook 失败
        self.shell.run_java_jar(self.apkeditor_path, ["d", "-f", "-i", str(jar), "-o", str(wd), "-no-dex-debug"])

        # ==========================================
        # 1. PropsHook 集成 (注入 classes.dex)
        # ==========================================
        props_hook_zip = Path("devices/common/PropsHook.zip")
        if props_hook_zip.exists():
            self.logger.info("Injecting PropsHook...")
            hook_tmp = self.temp_dir / "PropsHook"
            with zipfile.ZipFile(props_hook_zip, 'r') as z:
                z.extractall(hook_tmp)
            
            # 反编译 PropsHook 的 classes.dex
            classes_dex = hook_tmp / "classes.dex"
            if classes_dex.exists():
                classes_out = hook_tmp / "classes"
                self.shell.run_java_jar(self.baksmali_path, ["d", str(classes_dex), "-o", str(classes_out)])
                
                # 找到下一个 classesN 目录并复制
                self._copy_to_next_classes(wd, classes_out)

        # ==========================================
        # 2. StrictJarVerifier & 签名校验绕过
        # ==========================================
        self.logger.info("Applying Signature Bypass Patches...")
        
        # StrictJarVerifier.smali
        self._run_smalikit(path=str(wd), iname="StrictJarVerifier.smali", method="verifyMessageDigest([B[B)Z", remake=self.RETRUN_TRUE)
        # 构造函数注入: 禁用签名回滚保护
        self._run_smalikit(path=str(wd), iname="StrictJarVerifier.smali", 
                           method="<init>(Ljava/lang/String;Landroid/util/jar/StrictJarManifest;Ljava/util/HashMap;Z)V", 
                           before_line=["iput-boolean p4, p0, Landroid/util/jar/StrictJarVerifier;->signatureSchemeRollbackProtectionsEnforced:Z", "const/4 p4, 0x0"])

        # ApkSigningBlockUtils & Verifiers (V2, V3, V4)
        targets = [
            ("ApkSigningBlockUtils.smali", "verifyIntegrityFor1MbChunkBasedAlgorithm"),
            ("ApkSigningBlockUtils.smali", "verifyProofOfRotationStruct"),
            ("ApkSignatureSchemeV2Verifier.smali", "verifySigner"),
            ("ApkSignatureSchemeV3Verifier.smali", "verifySigner"),
            ("ApkSignatureSchemeV4Verifier.smali", "verifySigner"),
        ]
        s1 = "Ljava/security/MessageDigest;->isEqual([B[B)Z"
        s2 = "Ljava/security/Signature;->verify([B)Z"
        
        # 在 isEqual 和 verify 调用后强制返回 true
        # 对应 Shell: -al "$s1" "$INVOKE_TRUE"
        inject_code = f"{self.INVOKE_TRUE}\n    move-result v0\n    return v0" # 简化处理，直接返回
        # 注意：patches.sh 这里的 INVOKE_TRUE 只是调用了 Hook，没写 return，但在 Smali 中 invoke-static 后通常结果在 v0
        # Shell 脚本逻辑是 -al (After Line)。
        # 我们这里直接用 invoke 覆盖 result 寄存器可能更稳妥，或者按照 Shell 原样
        # Shell: $INVOKE_TRUE (只是调用). 但 HookHelper.RETURN_TRUE() 返回 boolean (Z)
        # 这里的意图是: isEqual 返回后，再次调用 RETURN_TRUE 覆盖 v0/result，然后代码继续走，或者直接返回
        
        for smali_file, method in targets:
             self._run_smalikit(path=str(wd), iname=smali_file, method=method, after_line=[s1, self.INVOKE_TRUE], recursive=True)
             self._run_smalikit(path=str(wd), iname=smali_file, method=method, after_line=[s2, self.INVOKE_TRUE], recursive=True)

        # PackageParser$SigningDetails & SigningDetails
        for m in ["checkCapability", "checkCapabilityRecover", "hasCommonAncestor", "signaturesMatchExactly"]:
            self._run_smalikit(path=str(wd), iname="PackageParser$SigningDetails.smali", method=m, remake=self.RETRUN_TRUE, recursive=True)
            self._run_smalikit(path=str(wd), iname="SigningDetails.smali", method=m, remake=self.RETRUN_TRUE, recursive=True)

        # AssetManager.smali
        self._run_smalikit(path=str(wd), iname="AssetManager.smali", method="containsAllocatedTable", remake=self.RETRUN_FALSE)

        # StrictJarFile.smali
        self._run_smalikit(path=str(wd), iname="StrictJarFile.smali", 
                           method="<init>(Ljava/lang/String;Ljava/io/FileDescriptor;ZZ)V", 
                           after_line=["move-result-object v6", "const/4 v6, 0x1"])

        # ApkSignatureVerifier.smali
        self._run_smalikit(path=str(wd), iname="ApkSignatureVerifier.smali", method="getMinimumSignatureSchemeVersionForTargetSdk", remake=self.RETRUN_TRUE)

        # ==========================================
        # 3. PIF Patch (Play Integrity Fix)
        # ==========================================
        pif_zip = Path("devices/common/pif_patch.zip")
        if pif_zip.exists():
            self._apply_pif_patch(wd, pif_zip)
        else:
            self.logger.warning("pif_patch.zip not found, skipping PIF injection.")

        # ==========================================
        # 4. PendingIntent Hook
        # ==========================================
        target_file = self._find_file_recursive(wd, "PendingIntent.smali")
        if target_file:
            hook_code = "\n    # [AutoCopy Hook]\n    invoke-static {p0, p2}, Lcom/android/internal/util/HookHelper;->onPendingIntentGetActivity(Landroid/content/Context;Landroid/content/Intent;)V"
            self._run_smalikit(file_path=str(target_file), method="getActivity(Landroid/content/Context;ILandroid/content/Intent;I)", insert_line=["2", hook_code])
            self._run_smalikit(file_path=str(target_file), method="getActivity(Landroid/content/Context;ILandroid/content/Intent;ILandroid/os/Bundle;)", insert_line=["2", hook_code])

        # ==========================================
        # 5. 自定义平台签名校验 (Custom Platform Key)
        # ==========================================
        self._integrate_custom_platform_key(wd)

        self._apkeditor_build(wd, jar)

        # --------------------------------------------------------------------------
        # PIF Patch 逻辑 (模拟 patches.sh)
        # --------------------------------------------------------------------------
    def _apply_pif_patch(self, work_dir, pif_zip):
        self.logger.info("Applying PIF Patch (Instrumentation, KeyStoreSpi, AppPM)...")
        
        # 1. 解压 PIF classes 到新的 classesX 目录
        temp_pif = self.temp_dir / "pif_classes"
        with zipfile.ZipFile(pif_zip, 'r') as z:
            z.extractall(temp_pif)
        self._copy_to_next_classes(work_dir, temp_pif / "classes")

        # 2. 修改 Instrumentation.smali
        inst_smali = self._find_file_recursive(work_dir, "Instrumentation.smali")
        if inst_smali:
            # 逻辑：找到 newApplication 方法中调用 attach 的地方，解析出 Context 寄存器，注入 setProps
            # Shell 使用 grep + awk 解析寄存器: grep "Landroid/app/Application;->attach(Landroid/content/Context;)V" | awk '{print $3}'
            
            # 读取文件内容用于分析
            content = inst_smali.read_text(encoding='utf-8', errors='ignore')
            
            # 目标方法 1
            method1 = "newApplication(Ljava/lang/ClassLoader;Ljava/lang/String;Landroid/content/Context;)Landroid/app/Application;"
            if method1 in content:
                # 提取寄存器：搜索该方法体内的 attach 调用
                # 示例: invoke-virtual {v0, p3}, Landroid/app/Application;->attach(Landroid/content/Context;)V
                # 我们需要 p3 (Context)
                reg = self._extract_register_from_invoke(content, method1, "Landroid/app/Application;->attach(Landroid/content/Context;)V", arg_index=1)
                if reg:
                    patch_code = f"    invoke-static {{{reg}}}, Lcom/android/internal/util/PropsHookUtils;->setProps(Landroid/content/Context;)V\n    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onNewApplication(Landroid/content/Context;)V"
                    # 在 return-object 之前插入
                    self._run_smalikit(file_path=str(inst_smali), method=method1, before_line=["return-object", patch_code])

            # 目标方法 2
            method2 = "newApplication(Ljava/lang/Class;Landroid/content/Context;)Landroid/app/Application;"
            if method2 in content:
                reg = self._extract_register_from_invoke(content, method2, "Landroid/app/Application;->attach(Landroid/content/Context;)V", arg_index=1)
                if reg:
                    patch_code = f"    invoke-static {{{reg}}}, Lcom/android/internal/util/PropsHookUtils;->setProps(Landroid/content/Context;)V\n    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onNewApplication(Landroid/content/Context;)V"
                    self._run_smalikit(file_path=str(inst_smali), method=method2, before_line=["return-object", patch_code])

        # 3. 修改 AndroidKeyStoreSpi.smali
        keystore_smali = self._find_file_recursive(work_dir, "AndroidKeyStoreSpi.smali")
        if keystore_smali:
            # A. 头部注入
            self._run_smalikit(file_path=str(keystore_smali), method="engineGetCertificateChain", 
                               insert_line=["2", "    invoke-static {}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onEngineGetCertificateChain()V"])
            
            # B. 尾部注入 (Spoof)
            # 逻辑：找到最后一个 aput-object {reg}, ...
            content = keystore_smali.read_text(encoding='utf-8')
            # 简单粗暴：直接读取最后一次出现 aput-object 的行
            aput_matches = list(re.finditer(r"aput-object\s+([vp]\d+),\s+([vp]\d+),\s+([vp]\d+)", content))
            if aput_matches:
                # 必须确认这个 aput-object 是在 engineGetCertificateChain 方法内的
                # 这里简化处理：假设该文件主要逻辑就是这个，或者我们只处理该方法内的
                # 使用 SmaliKit 的 regex_replace 做精准替换有点难，因为上下文
                # 我们手动 patch
                
                # 提取 engineGetCertificateChain 方法体
                pattern = re.compile(r"(\.method.+engineGetCertificateChain.+?\.end method)", re.DOTALL)
                match = pattern.search(content)
                if match:
                    body = match.group(1)
                    # 在方法体内找最后一个 aput-object
                    inner_aputs = list(re.finditer(r"aput-object\s+([vp]\d+),\s+([vp]\d+),\s+([vp]\d+)", body))
                    if inner_aputs:
                        last_aput = inner_aputs[-1]
                        array_reg = last_aput.group(2)
                        
                        spoof_code = f"\n    invoke-static {{{array_reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->genCertificateChain([Ljava/security/cert/Certificate;)[Ljava/security/cert/Certificate;\n    move-result-object {array_reg}\n"
                        
                        # 插入到 aput-object 这一行之后
                        # 替换整个 aput-object 行 -> aput-object \n spoof_code
                        old_line = last_aput.group(0)
                        new_body = body.replace(old_line, old_line + spoof_code)
                        content = content.replace(body, new_body)
                        keystore_smali.write_text(content, encoding='utf-8')

        app_pm_smali = self._find_file_recursive(work_dir, "ApplicationPackageManager.smali")
        if app_pm_smali:
            # 注入 hasSystemFeature Hook
            self.logger.info("Hooking ApplicationPackageManager...")
            
            method_sig = "hasSystemFeature(Ljava/lang/String;I)Z"
            
            # [优化] 动态匹配返回寄存器 (\1)
            # 正则逻辑：
            # 1. 匹配 return [vp]X
            # 2. 将 [vp]X 捕获为组 \1
            # 3. 在 Hook 代码中使用 \1 来代替写死的 v1
            
            # 说明：
            # {p1, \\1} -> 传入 Feature名(p1) 和 原始结果寄存器
            # move-result \\1 -> 将 Hook 后的结果存回该寄存器
            # return \\1 -> 返回该寄存器
            
            repl_pattern = (
                r"invoke-static {p1, \1}, Lcom/android/internal/util/PropsHookUtils;->hasSystemFeature(Ljava/lang/String;Z)Z"
                r"\n    move-result \1"
                r"\n    return \1"
            )
            
            self._run_smalikit(
                file_path=str(app_pm_smali), 
                method=method_sig, 
                regex_replace=(r"return\s+([vp]\d+)", repl_pattern)
            )
        # 5. SELinux Policy & Contexts
        policy_tool = self.bin_dir / "insert_selinux_policy.py"
        config_json = Path("devices/common/pif_updater_policy.json")
        cil_path = self.ctx.target_dir / "system/system/etc/selinux/plat_sepolicy.cil"
        
        if policy_tool.exists() and config_json.exists() and cil_path.exists():
            self.shell.run(["python3", str(policy_tool), "--config", str(config_json), str(cil_path)])
            
            # Append file_contexts
            fc_path = self.ctx.target_dir / "system/system/etc/selinux/plat_file_contexts"
            if fc_path.exists():
                with open(fc_path, "a") as f:
                    f.write("\n/system/bin/pif-updater       u:object_r:pif_updater_exec:s0\n")
                    f.write("/data/system/pif_tmp.apk  u:object_r:pif_data_file:s0\n")
                    f.write("/data/PIF.apk u:object_r:pif_data_file:s0\n")
                    f.write("/data/local/tmp/PIF.apk   u:object_r:pif_data_file:s0\n")
        
        # 6. Build Props
        product_prop = self.ctx.target_dir / "product/etc/build.prop"
        if product_prop.exists():
            with open(product_prop, "a") as f:
                f.write("\npersist.sys.oemports10t.pif.autoupdate=true\n")
                f.write("persist.sys.oemports10t.blspoof=true\n")
                f.write("persist.sys.oemports10t.fpspoof=true\n")
                f.write("persist.sys.oemports10t.utils-debug=true\n")

    # --------------------------------------------------------------------------
    # 自定义平台签名校验逻辑
    # --------------------------------------------------------------------------
    def _integrate_custom_platform_key(self, work_dir):
        epm_smali = self._find_file_recursive(work_dir, "ExtraPackageManager.smali")
        if not epm_smali: return
        self.logger.info("Injecting Custom Platform Key Check...")

        # 你的自定义 Key (从 patches.sh 提取)
        MY_PLATFORM_KEY = "308203bb308202a3a00302010202146a0b4f6a1a8f61a32d8450ead92d479dea486573300d06092a864886f70d01010b0500306c310b300906035504061302434e3110300e06035504080c075369436875616e3110300e06035504070c074368656e6744753110300e060355040a0c07504f5254524f4d31133011060355040b0c0a4d61696e7461696e65723112301006035504030c09427275636554656e673020170d3236303230323031333632385a180f32303533303632303031333632385a306c310b300906035504061302434e3110300e06035504080c075369436875616e3110300e06035504070c074368656e6744753110300e060355040a0c07504f5254524f4d31133011060355040b0c0a4d61696e7461696e65723112301006035504030c09427275636554656e6730820122300d06092a864886f70d01010105000382010f003082010a0282010100cb68bcf8927a175624a0a7428f1bbd67b4cf18c8ba42b73de9649fd2aa42935b9195b27ccd611971056654db51499ffa01783a1dbc95e03f9c557d4930193c3d04f9016a84411b502ea844fac9d463b4c9eed2d73ca3267b8a399f5da254941c7413d2a7534fd30a4ed10567933bfda249e2027ce74da667de3b6278844d232e038c2c98deb7d172a44b2fd9ec90ea74cb1c96b647044c60ce18cec93b60b84065ddd8800e10bcf465e4f3ace6d423ef2b235d75081e36b5d0f1ca858090d3dd8d74437ebb504490a8e7e9e3e2b696c3ac8e2ec856bedf4efe4e05e14f2437f81fbc8428aa330cdde0816450b4416e10f743204c17ee65b92ebc61799b4cf42b0203010001a3533051301d0603551d0e041604140a318d86cc0040341341b6dc716094da06cd4dd6301f0603551d230418301680140a318d86cc0040341341b6dc716094da06cd4dd6300f0603551d130101ff040530030101ff300d06092a864886f70d01010b0500038201010023e7aeda5403f40c794504e3edf99182a5eb53c9ddec0d93fd9fe6539e1520ea6ad08ac3215555f3fe366fa6ab01e0f45d6ce1512416c572f387a72408dde6442b76e405296cc8c128844fe68a29f6a114eb6f303e3545ea0b32d85e9c7d45cfa3c860b03d00171bb2aa4434892bf484dd390643f324a2e38a5e6ce7f26e92b3d02ac8605514b9c75a8aab9ab990c01951213f7214a36389c0759cfb68737bb3bb85dff4b1b40377279e2c82298351c276ab266869d6494b838bd6cc175185f705b8806eb1950becec57fb4f9b50240bb92d1d30bbb5764d311d18446588e5fd2b9785c635f2bb690df1e4fb595305371350c6d306d3f6cae3bc4974e9d8609c"
        
        hook_code = f"""
    # [Start] Custom Platform Key Check
    const/4 v2, 0x1
    new-array v2, v2, [Landroid/content/pm/Signature;
    new-instance v3, Landroid/content/pm/Signature;
    const-string v4, "{MY_PLATFORM_KEY}"
    invoke-direct {{v3, v4}}, Landroid/content/pm/Signature;-><init>(Ljava/lang/String;)V
    const/4 v4, 0x0
    aput-object v3, v2, v4
    invoke-static {{p0, v2}}, Lmiui/content/pm/ExtraPackageManager;->compareSignatures([Landroid/content/pm/Signature;[Landroid/content/pm/Signature;)I
    move-result v2
    if-eqz v2, :cond_custom_skip
    const/4 v2, 0x1
    return v2
    :cond_custom_skip
    # [End]"""

        # 1. 扩容 locals (.locals X -> .locals 5)
        self._run_smalikit(file_path=str(epm_smali), method="isTrustedPlatformSignature([Landroid/content/pm/Signature;)Z", 
                           regex_replace=(r"\.locals\s+\d+", ".locals 5"))
        
        # 2. 插入代码 (在第 2 行，即方法入口处)
        self._run_smalikit(file_path=str(epm_smali), method="isTrustedPlatformSignature([Landroid/content/pm/Signature;)Z", 
                           insert_line=["2", hook_code])

    # --------------------------------------------------------------------------
    # 辅助方法：复制 Dex 内容到下一个 available 的 classes 目录
    # --------------------------------------------------------------------------
    def _copy_to_next_classes(self, work_dir, source_dir):
        max_num = 1
        for d in work_dir.glob("smali/classes*"):
             name = d.name
             if name == "classes": num = 1
             else: 
                 try: num = int(name.replace("classes", ""))
                 except: num = 1
             if num > max_num: max_num = num
        
        target = work_dir / "smali" / f"classes{max_num + 1}"
        shutil.copytree(source_dir, target, dirs_exist_ok=True)
        self.logger.info(f"Copied classes to {target.name}")

    # --------------------------------------------------------------------------
    # 辅助方法：从 invoke-virtual/static 语句中解析寄存器
    # --------------------------------------------------------------------------
    def _extract_register_from_invoke(self, content, method_name, invoke_sig, arg_index):
        """
        在指定方法的 Smali 代码中，找到调用 invoke_sig 的行，并提取第 arg_index 个参数的寄存器
        示例: invoke-virtual {v0, p3}, ...
        arg_index=0 -> v0, arg_index=1 -> p3
        """
        # 1. 提取方法体
        pattern = re.compile(rf"(\.method.+{re.escape(method_name)}.+?\.end method)", re.DOTALL)
        match = pattern.search(content)
        if not match: return None
        body = match.group(1)
        
        # 2. 查找 invoke 语句
        # 匹配 invoke-virtual {v0, v1}, L...
        sig_escaped = re.escape(invoke_sig)
        invoke_pattern = re.compile(r"invoke-.*?\s*\{(.*?)\},\s*" + sig_escaped)
        invoke_match = invoke_pattern.search(body)
        
        if invoke_match:
            args_str = invoke_match.group(1) # "v0, p3"
            args = [a.strip() for a in args_str.split(",")]
            if len(args) > arg_index:
                return args[arg_index]
        return None
    
    def _inject_xeu_toolbox(self):
        """
        注入 Xiaomi.eu Toolbox (集成 xeutoolbox.zip)
        对应 Shell: if [[ -f devices/common/xeutoolbox.zip ]] ...
        """
        xeu_zip = Path("devices/common/xeutoolbox.zip")
        if not xeu_zip.exists():
            return

        self.logger.info("Injecting Xiaomi.eu Toolbox...")

        # 1. 解压 zip 到 target_dir
        # Shell: unzip -o devices/common/xeutoolbox.zip -d build/portrom/images/
        try:
            with zipfile.ZipFile(xeu_zip, 'r') as z:
                z.extractall(self.ctx.target_dir)
            self.logger.info(f"Extracted {xeu_zip.name}")
        except Exception as e:
            self.logger.error(f"Failed to extract xeutoolbox: {e}")
            return

        # 2. 修改 system_ext_file_contexts
        # Shell: echo "/system_ext/xbin/xeu_toolbox ..." >> ...
        # 注意：这里有两个路径，一个是 images/config/... (可能用于repack)，一个是 images/system_ext/etc/... (实际分区内)
        
        target_files = [
            self.ctx.target_dir / "config/system_ext_file_contexts",
            self.ctx.target_dir / "system_ext/etc/selinux/system_ext_file_contexts"
        ]
        
        context_line = "\n/system_ext/xbin/xeu_toolbox  u:object_r:toolbox_exec:s0\n"

        for f in target_files:
            if f.exists():
                try:
                    with open(f, "a", encoding="utf-8") as file:
                        file.write(context_line)
                    self.logger.info(f"Updated contexts: {f.name}")
                except Exception as e:
                    self.logger.warning(f"Failed to append context to {f}: {e}")

        # 3. 修改 system_ext_sepolicy.cil
        # Shell: echo "(allow init toolbox_exec (file ((execute_no_trans))))" >> ...
        cil_file = self.ctx.target_dir / "system_ext/etc/selinux/system_ext_sepolicy.cil"
        policy_line = "\n(allow init toolbox_exec (file ((execute_no_trans))))\n"
        
        if cil_file.exists():
            try:
                with open(cil_file, "a", encoding="utf-8") as f:
                    f.write(policy_line)
                self.logger.info(f"Updated sepolicy: {cil_file.name}")
            except Exception as e:
                self.logger.warning(f"Failed to append policy to {cil_file}: {e}")