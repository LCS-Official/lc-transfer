/* LC 互传, 2026. Apache-2.0. */
package org.primftpd.transfer;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Environment;
import org.json.JSONObject;
import org.primftpd.crypto.HostKeyAlgorithm;
import org.primftpd.prefs.LoadPrefsUtil;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.util.Collections;
import java.util.Locale;
import java.util.Base64;

/** The small public settings surface; SSH credentials never leave app-private storage. */
public final class TransferConfig {
    public static final String RECEIVE = "lc_receive_dir";
    public static final String OPEN_APP = "lc_open_component";
    public static final String PC_KEY = "lc_pc_key";
    public static final String PC_NAME = "lc_pc_name";
    public static final String DEFAULT_RECEIVE = "/storage/emulated/0/Download/互传";
    private TransferConfig() {}

    public static SharedPreferences prefs(Context context) { return LoadPrefsUtil.getPrefs(context); }
    public static String receiveDir(Context context) { return prefs(context).getString(RECEIVE, DEFAULT_RECEIVE); }
    public static File stateFile(String name) {
        return new File(Environment.getExternalStorageDirectory(), ".lc-transfer/" + name);
    }

    public static void prepareNetwork(Context context) {
        SharedPreferences p = prefs(context);
        if (p.getBoolean("lc_lan_v1", false)) return;
        String ip = p.getString(LoadPrefsUtil.PREF_KEY_BIND_IP, "0.0.0.0");
        p.edit().putString(LoadPrefsUtil.PREF_KEY_BIND_IP, ip).putBoolean("lc_lan_v1", true).commit();
    }

    public static void prepare(Context context) throws Exception {
        prepareNetwork(context);
        SharedPreferences p = prefs(context);
        SharedPreferences.Editor edit = p.edit()
            .putString(LoadPrefsUtil.PREF_KEY_USER, "user")
            .putString(LoadPrefsUtil.PREF_KEY_PASSWORD, "")
            .putBoolean(LoadPrefsUtil.PREF_ANONYMOUS_LOGIN, false)
            .putBoolean(LoadPrefsUtil.PREF_KEY_PUB_KEY_AUTH, true)
            .putString(LoadPrefsUtil.PREF_KEY_WHICH_SERVER, "2")
            .putString(LoadPrefsUtil.PREF_KEY_SECURE_PORT, "52022")
            .putString(LoadPrefsUtil.PREF_KEY_STORAGE_TYPE, "1")
            .putString(LoadPrefsUtil.PREF_KEY_START_DIR, Environment.getExternalStorageDirectory().getAbsolutePath())
            .putString(LoadPrefsUtil.PREF_KEY_IDLE_TIMEOUT_SERVER_STOP, "0")
            .putString(LoadPrefsUtil.PREF_KEY_IDLE_TIMEOUT, "0")
            .putString(LoadPrefsUtil.PREF_KEY_ALLOWED_IPS_PATTERN, "")
            .putBoolean(LoadPrefsUtil.PREF_KEY_CHOOSE_BIND_IP, false)
            .putBoolean(LoadPrefsUtil.PREF_KEY_ANNOUNCE, false)
            .putBoolean(LoadPrefsUtil.PREF_KEY_START_ON_BOOT, false)
            .putBoolean(LoadPrefsUtil.PREF_KEY_START_ON_OPEN, false)
            .putBoolean(LoadPrefsUtil.PREF_KEY_SHOW_CONN_INFO, false)
            .putStringSet(LoadPrefsUtil.PREF_KEY_HOSTKEY_ALGOS, Collections.singleton("ed25519"));
        edit.commit();
        HostKeyAlgorithm algorithm = HostKeyAlgorithm.ED_25519;
        if (!new File(context.getFilesDir(), algorithm.getFilenamePrivateKey()).exists()
                || !new File(context.getFilesDir(), algorithm.getFilenamePublicKey()).exists()) {
            try (FileOutputStream pub = context.openFileOutput(algorithm.getFilenamePublicKey(), Context.MODE_PRIVATE);
                 FileOutputStream priv = context.openFileOutput(algorithm.getFilenamePrivateKey(), Context.MODE_PRIVATE)) {
                algorithm.generateKey(pub, priv);
            }
        }
        writeDevice(context);
    }

    public static File validateReceive(String value) throws Exception {
        if (value == null || !value.startsWith("/")) throw new IllegalArgumentException("请填写完整的手机目录路径");
        File file = new File(value).getCanonicalFile();
        String root = Environment.getExternalStorageDirectory().getCanonicalPath();
        String path = file.getPath();
        if (!path.startsWith(root + "/") || path.startsWith(root + "/Android/"))
            throw new IllegalArgumentException("接收目录需位于共享存储中，且不能位于 Android 目录内");
        if ((!file.isDirectory() && !file.mkdirs()) || !file.canWrite())
            throw new IllegalArgumentException("无法写入此目录，请检查所有文件访问权限");
        return file;
    }

    public static void writeDevice(Context context) throws Exception {
        File target = validateReceive(receiveDir(context));
        File file = stateFile("device.json");
        if (!file.getParentFile().isDirectory() && !file.getParentFile().mkdirs())
            throw new IllegalStateException("无法创建传输记录目录");
        JSONObject object = new JSONObject().put("receive_dir", target.getPath()).put("media_catalog", 1)
            .put("preview_edge", MediaCatalog.PREVIEW_EDGE).put("system_share", 1);
        Files.write(file.toPath(), object.toString().getBytes(StandardCharsets.UTF_8));
    }

    public static String fingerprint(byte[] key) throws Exception {
        return "SHA256:" + Base64.getEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest(key));
    }
    public static String hostFingerprint(Context context) throws Exception {
        String key = new String(Files.readAllBytes(new File(context.getFilesDir(), "id_ed25519.pub").toPath()), StandardCharsets.UTF_8);
        return fingerprint(Base64.getDecoder().decode(key.trim().split("\\s+")[1]));
    }
    public static String pairingCode(String phone, String pc) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest((phone + "\n" + pc).getBytes(StandardCharsets.UTF_8));
        long value = ((long)(digest[0] & 255) << 24) | ((long)(digest[1] & 255) << 16)
            | ((long)(digest[2] & 255) << 8) | (digest[3] & 255);
        return String.format(Locale.ROOT, "%06d", value % 1000000);
    }
    public static boolean authorized(Context context, String username, PublicKey key) {
        String saved = prefs(context).getString(PC_KEY, "");
        if (!"user".equals(username) || saved.isEmpty()) return false;
        return matchesEncoded(saved, key);
    }
    public static boolean matchesEncoded(String saved, PublicKey key) {
        return !saved.isEmpty() && MessageDigest.isEqual(saved.getBytes(StandardCharsets.US_ASCII),
            Base64.getEncoder().encode(key.getEncoded()));
    }
}
