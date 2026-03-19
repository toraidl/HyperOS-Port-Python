"""HTMLViewer modification plugin for EU ROMs."""

from pathlib import Path

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry


@ApkModifierRegistry.register
class HTMLViewerModifier(ApkModifierPlugin):
    """Modify HTMLViewer.apk for EU ROM device info localization."""

    name = "htmlviewer_modifier"
    description = "Add locale-based JSON loading for device info in EU ROMs"
    apk_name = "HTMLViewer"
    package_name = "com.android.htmlviewer"
    priority = 100
    parallel_safe = True
    cache_version = "1.1"

    def check_prerequisites(self) -> bool:
        if not super().check_prerequisites():
            return False

        is_eu = getattr(self.ctx, "is_port_eu_rom", False)
        if not is_eu:
            self.logger.debug("Not an EU ROM, skipping HTMLViewer modification")
            return False

        return True

    def _apply_patches(self, work_dir: Path):
        self.logger.info("Applying EU HTMLViewer patches...")
        self._patch_device_info_utils(work_dir)

    def _patch_device_info_utils(self, work_dir: Path):
        smali_file = self._find_file(work_dir, "MiuiDeviceInfoUtils$AsyncTask.smali")

        if not smali_file:
            self.logger.warning("MiuiDeviceInfoUtils$AsyncTask.smali not found")
            return

        content = smali_file.read_text(encoding="utf-8")

        if "doInBackground([Landroid/util/Pair;)Ljava/lang/Object;" not in content:
            self.logger.warning("doInBackground method not found")
            return

        self.logger.info("Patching MiuiDeviceInfoUtils$AsyncTask.smali...")

        do_in_background_remake = """.locals 16
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "([",
            "Landroid/util/Pair<",
            "Ljava/lang/String;",
            "Ljava/lang/String;",
            ">;)",
            "Ljava/lang/Object;"
        }
    .end annotation

    move-object/from16 v1, p1
    const-string v0, "status"
    const-string v2, "camera"
    array-length v3, v1
    const/4 v4, 0x0
    if-lez v3, :cond_e
    :try_start_0
    const-string v3, "/product/etc/device_info.json"
    invoke-static {v3}, Lcom/android/settings/services/MiuiDeviceInfoUtils;->getFileContent(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v3
    new-instance v5, Lorg/json/JSONTokener;
    invoke-direct {v5, v3}, Lorg/json/JSONTokener;-><init>(Ljava/lang/String;)V
    invoke-virtual {v5}, Lorg/json/JSONTokener;->nextValue()Ljava/lang/Object;
    move-result-object v3
    instance-of v5, v3, Lorg/json/JSONArray;
    const/4 v6, 0x0
    if-eqz v5, :cond_7
    check-cast v3, Lorg/json/JSONArray;
    move-object v7, v4
    move v5, v6
    :goto_0
    invoke-virtual {v3}, Lorg/json/JSONArray;->length()I
    move-result v8
    if-ge v5, v8, :cond_8
    invoke-virtual {v3, v5}, Lorg/json/JSONArray;->getJSONObject(I)Lorg/json/JSONObject;
    move-result-object v8
    array-length v9, v1
    move v10, v6
    :goto_1
    if-ge v10, v9, :cond_5
    aget-object v11, v1, v10
    iget-object v12, v11, Landroid/util/Pair;->first:Ljava/lang/Object;
    check-cast v12, Ljava/lang/String;
    invoke-virtual {v8, v12}, Lorg/json/JSONObject;->isNull(Ljava/lang/String;)Z
    move-result v12
    if-eqz v12, :cond_0
    goto :goto_4
    :cond_0
    iget-object v12, v11, Landroid/util/Pair;->first:Ljava/lang/Object;
    check-cast v12, Ljava/lang/String;
    invoke-virtual {v8, v12}, Lorg/json/JSONObject;->get(Ljava/lang/String;)Ljava/lang/Object;
    move-result-object v12
    instance-of v13, v12, Lorg/json/JSONObject;
    if-eqz v13, :cond_1
    :goto_2
    move-object v8, v4
    goto :goto_5
    :cond_1
    new-instance v13, Ljava/util/HashSet;
    invoke-direct {v13}, Ljava/util/HashSet;-><init>()V
    instance-of v14, v12, Lorg/json/JSONArray;
    if-eqz v14, :cond_2
    check-cast v12, Lorg/json/JSONArray;
    move v14, v6
    :goto_3
    invoke-virtual {v12}, Lorg/json/JSONArray;->length()I
    move-result v15
    if-ge v14, v15, :cond_3
    invoke-virtual {v12, v14}, Lorg/json/JSONArray;->getString(I)Ljava/lang/String;
    move-result-object v15
    invoke-virtual {v13, v15}, Ljava/util/HashSet;->add(Ljava/lang/Object;)Z
    add-int/lit8 v14, v14, 0x1
    goto :goto_3
    :cond_2
    invoke-virtual {v12}, Ljava/lang/Object;->toString()Ljava/lang/String;
    move-result-object v12
    invoke-virtual {v13, v12}, Ljava/util/HashSet;->add(Ljava/lang/Object;)Z
    :cond_3
    iget-object v11, v11, Landroid/util/Pair;->second:Ljava/lang/Object;
    invoke-virtual {v13, v11}, Ljava/util/HashSet;->contains(Ljava/lang/Object;)Z
    move-result v11
    if-nez v11, :cond_4
    goto :goto_2
    :cond_4
    :goto_4
    add-int/lit8 v10, v10, 0x1
    goto :goto_1
    :cond_5
    :goto_5
    if-eqz v8, :cond_6
    move-object v7, v8
    :cond_6
    add-int/lit8 v5, v5, 0x1
    goto :goto_0
    :cond_7
    move-object v7, v3
    check-cast v7, Lorg/json/JSONObject;
    :cond_8
    if-eqz v7, :cond_d
    new-instance v3, Lorg/json/JSONObject;
    invoke-direct {v3}, Lorg/json/JSONObject;-><init>()V
    :try_end_0
    .catch Ljava/lang/Exception; {:try_start_0 .. :try_end_0} :catch_3
    move-object/from16 v5, p0
    :try_start_1
    iget v8, v5, Lcom/android/settings/services/MiuiDeviceInfoUtils$AsyncTask;->type:I
    :try_end_1
    .catch Ljava/lang/Exception; {:try_start_1 .. :try_end_1} :catch_2
    const-string v9, "1"
    const-string v10, "BasicInfoToggle"
    if-nez v8, :cond_b
    :try_start_2
    const-string v0, "basic"
    invoke-virtual {v7, v0}, Lorg/json/JSONObject;->getJSONObject(Ljava/lang/String;)Lorg/json/JSONObject;
    move-result-object v0
    new-instance v2, Lorg/json/JSONArray;
    invoke-direct {v2}, Lorg/json/JSONArray;-><init>()V
    :goto_6
    sget-object v7, Lcom/android/settings/services/MiuiDeviceInfoUtils;->BASIC_ITEMS:[Ljava/lang/String;
    array-length v7, v7
    if-ge v6, v7, :cond_a
    sget-object v7, Lcom/android/settings/services/MiuiDeviceInfoUtils;->BASIC_ITEMS:[Ljava/lang/String;
    aget-object v7, v7, v6
    new-instance v8, Lorg/json/JSONObject;
    invoke-direct {v8}, Lorg/json/JSONObject;-><init>()V
    const-string v11, "Title"
    invoke-virtual {v8, v11, v7}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    const-string v11, "Summary"
    invoke-virtual {v0, v7}, Lorg/json/JSONObject;->has(Ljava/lang/String;)Z
    move-result v12
    if-eqz v12, :cond_9
    invoke-virtual {v0, v7}, Lorg/json/JSONObject;->get(Ljava/lang/String;)Ljava/lang/Object;
    move-result-object v7
    invoke-direct {v5, v7}, Lcom/android/settings/services/MiuiDeviceInfoUtils$AsyncTask;->getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;
    move-result-object v7
    goto :goto_7
    :cond_9
    const-string v7, ""
    :goto_7
    invoke-virtual {v8, v11, v7}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    const-string v7, "Index"
    invoke-static {v6}, Ljava/lang/Integer;->toString(I)Ljava/lang/String;
    move-result-object v11
    invoke-virtual {v8, v7, v11}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    invoke-virtual {v2, v8}, Lorg/json/JSONArray;->put(Ljava/lang/Object;)Lorg/json/JSONArray;
    add-int/lit8 v6, v6, 0x1
    goto :goto_6
    :cond_a
    invoke-virtual {v3, v10, v9}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    const-string v0, "BasicItems"
    invoke-virtual {v3, v0, v2}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    :try_end_2
    .catch Ljava/lang/Exception; {:try_start_2 .. :try_end_2} :catch_1
    goto :goto_8
    :cond_b
    const/4 v6, 0x1
    if-ne v8, v6, :cond_c
    :try_start_3
    invoke-virtual {v7, v2}, Lorg/json/JSONObject;->getJSONObject(Ljava/lang/String;)Lorg/json/JSONObject;
    move-result-object v6
    new-instance v7, Lorg/json/JSONObject;
    invoke-direct {v7}, Lorg/json/JSONObject;-><init>()V
    invoke-virtual {v7, v10, v9}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    invoke-virtual {v7, v2, v6}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    const-string v2, "data"
    invoke-virtual {v3, v2, v7}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    const-string v2, "true"
    invoke-virtual {v3, v0, v2}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    :try_end_3
    .catch Ljava/lang/Exception; {:try_start_3 .. :try_end_3} :catch_0
    goto :goto_8
    :catch_0
    :try_start_4
    const-string v2, "false"
    invoke-virtual {v3, v0, v2}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;
    :catch_1
    :cond_c
    :goto_8
    invoke-virtual {v3}, Lorg/json/JSONObject;->toString()Ljava/lang/String;
    move-result-object v0
    return-object v0
    :cond_d
    move-object/from16 v5, p0
    new-instance v0, Ljava/lang/NullPointerException;
    invoke-direct {v0}, Ljava/lang/NullPointerException;-><init>()V
    throw v0
    :try_end_4
    .catch Ljava/lang/Exception; {:try_start_4 .. :try_end_4} :catch_2
    :catch_2
    move-exception v0
    goto :goto_9
    :catch_3
    move-exception v0
    move-object/from16 v5, p0
    :goto_9
    new-instance v2, Ljava/lang/StringBuilder;
    invoke-direct {v2}, Ljava/lang/StringBuilder;-><init>()V
    const-string v3, "Failed to get device info for "
    invoke-virtual {v2, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v2
    invoke-static/range {p1 .. p1}, Ljava/util/Arrays;->toString([Ljava/lang/Object;)Ljava/lang/String;
    move-result-object v1
    invoke-virtual {v2, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v1
    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1
    const-string v2, "MiuiDeviceInfoUtils"
    invoke-static {v2, v1, v0}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I
    goto :goto_a
    :cond_e
    move-object/from16 v5, p0
    :goto_a
    return-object v4
"""

        helper_method = """.method private getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;
    .locals 4
    .param p1, "value"

    instance-of v0, p1, Lorg/json/JSONObject;
    if-eqz v0, :cond_3
    check-cast p1, Lorg/json/JSONObject;
    invoke-static {}, Ljava/util/Locale;->getDefault()Ljava/util/Locale;
    move-result-object v3
    invoke-virtual {v3}, Ljava/util/Locale;->getLanguage()Ljava/lang/String;
    move-result-object v0
    const-string v1, "zh"
    invoke-virtual {v1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v1
    if-eqz v1, :cond_0
    invoke-virtual {v3}, Ljava/util/Locale;->getCountry()Ljava/lang/String;
    move-result-object v1
    const-string v2, "TW"
    invoke-virtual {v2, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v2
    if-nez v2, :cond_0
    const-string v2, "HK"
    invoke-virtual {v2, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v1
    if-eqz v1, :cond_1
    :cond_0
    const-string v0, "en"
    :cond_1
    invoke-virtual {p1, v0}, Lorg/json/JSONObject;->has(Ljava/lang/String;)Z
    move-result v1
    if-eqz v1, :cond_2
    invoke-virtual {p1, v0}, Lorg/json/JSONObject;->optString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object p1
    return-object p1
    :cond_2
    const-string v0, "en"
    invoke-virtual {p1, v0}, Lorg/json/JSONObject;->optString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object p1
    return-object p1
    :cond_3
    invoke-virtual {p1}, Ljava/lang/Object;->toString()Ljava/lang/String;
    move-result-object p1
    return-object p1
.end method
"""

        helper_signature = "getLocalizedValue(Ljava/lang/Object;)Ljava/lang/String;"
        self.smali_patch(
            work_dir,
            file_path=str(smali_file),
            method="doInBackground([Landroid/util/Pair;)Ljava/lang/Object;",
            return_type="Ljava/lang/Object;",
            remake=do_in_background_remake,
        )
        self.smali_patch(
            work_dir,
            file_path=str(smali_file),
            append_method=(helper_signature, helper_method),
        )

        self.logger.info("Patched MiuiDeviceInfoUtils$AsyncTask.smali successfully")
