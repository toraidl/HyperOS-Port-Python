import logging

from src.utils.smalikit import SmaliArgs, SmaliKit


def test_append_method_when_missing():
    content = """
.class public Lcom/example/Test;
.super Ljava/lang/Object;

.method public test()V
    .locals 0
    return-void
.end method
""".strip()
    signature = "getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;"
    method_block = """
.method private getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;
    .locals 1
    const-string v0, ""
    return-object v0
.end method
""".strip()

    args = SmaliArgs(append_method=(signature, method_block))
    patcher = SmaliKit(args, logger=logging.getLogger("test.smalikit"))

    new_content, patched = patcher.process_content(content, "Test.smali")

    assert patched is True
    assert signature in new_content
    assert new_content.count(".method private getLocalizedValue") == 1


def test_append_method_skips_when_exists():
    signature = "getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;"
    existing_method = """
.method private getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;
    .locals 1
    const-string v0, ""
    return-object v0
.end method
""".strip()
    content = f"""
.class public Lcom/example/Test;
.super Ljava/lang/Object;

{existing_method}
""".strip()

    args = SmaliArgs(append_method=(signature, existing_method))
    patcher = SmaliKit(args, logger=logging.getLogger("test.smalikit"))

    new_content, patched = patcher.process_content(content, "Test.smali")

    assert patched is False
    assert new_content == content


def test_append_method_does_not_treat_invoke_reference_as_existing_method():
    signature = "getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;"
    method_block = """
.method private getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;
    .locals 1
    const-string v0, ""
    return-object v0
.end method
""".strip()
    content = """
.class public Lcom/example/Test;
.super Ljava/lang/Object;

.method public demo(Ljava/lang/Object;)Ljava/lang/String;
    .locals 1
    invoke-direct {p0, p1}, Lcom/example/Test;->getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;
    move-result-object v0
    return-object v0
.end method
""".strip()

    args = SmaliArgs(append_method=(signature, method_block))
    patcher = SmaliKit(args, logger=logging.getLogger("test.smalikit"))

    new_content, patched = patcher.process_content(content, "Test.smali")

    assert patched is True
    assert new_content.count(".method private getLocalizedValue") == 1
