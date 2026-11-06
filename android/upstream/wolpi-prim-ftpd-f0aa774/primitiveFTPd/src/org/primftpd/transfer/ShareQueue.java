/* System Sharesheet to the existing authenticated SFTP connection. Apache-2.0. */
package org.primftpd.transfer;

import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;
import android.os.Parcelable;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONArray;
import org.json.JSONObject;

/** Copy granted content while the receiving activity owns its temporary URI permissions. */
public final class ShareQueue {
    private static final ExecutorService worker = Executors.newSingleThreadExecutor();
    private static volatile String message = "";
    private static volatile int preparing;
    private ShareQueue() { }

    public static boolean accepts(Intent intent) {
        return intent != null && (Intent.ACTION_SEND.equals(intent.getAction())
                || Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction()));
    }

    public static List<Uri> streams(Intent intent) throws IOException {
        LinkedHashSet<Uri> unique = new LinkedHashSet<>();
        if (!accepts(intent)) throw new IOException("不支持的分享类型");
        if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Parcelable> values = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (values != null) for (Parcelable value : values) {
                if (!(value instanceof Uri)) throw new IOException("分享内容不是文件");
                unique.add((Uri)value);
            }
        } else {
            Parcelable value = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (value != null) {
                if (!(value instanceof Uri)) throw new IOException("分享内容不是文件");
                unique.add((Uri)value);
            }
        }
        ClipData clip = intent.getClipData();
        if (unique.isEmpty() && clip != null) for (int i = 0; i < clip.getItemCount(); i++) {
            Uri uri = clip.getItemAt(i).getUri();
            if (uri != null) unique.add(uri);
        }
        if (unique.isEmpty()) throw new IOException("这次分享没有可读取的文件，请选择文件或安装包后再分享");
        if (unique.size() > 1000) throw new IOException("一次最多分享 1000 个文件");
        for (Uri uri : unique) if (!"content".equals(uri.getScheme()) && !"file".equals(uri.getScheme()))
            throw new IOException("仅支持文件分享，不接收网页链接");
        return new ArrayList<>(unique);
    }

    public static String safeName(String name) {
        if (name == null) name = "";
        name = name.replaceAll("[\\\\/\\x00-\\x1f]", "_").trim();
        if (name.isEmpty() || name.equals(".") || name.equals("..")) name = "shared-file";
        return name.length() > 240 ? name.substring(0, 240) : name;
    }

    public static File sharedFile(File file, File root) throws IOException {
        File target = file.getCanonicalFile();
        String prefix = root.getCanonicalPath() + File.separator;
        if (!target.getPath().startsWith(prefix) || target.getPath().startsWith(prefix + ".lc-transfer" + File.separator)
                || !target.isFile()) throw new IOException("无法分享此路径；请由文件应用提供可读的文件分享链接");
        return target;
    }

    public static long copyTo(InputStream input, File target) throws IOException {
        if (input == null) throw new IOException("来源应用未提供可读取的文件");
        long size = 0;
        boolean complete = false;
        try (FileOutputStream output = new FileOutputStream(target)) {
            byte[] block = new byte[1024 * 1024];
            int count;
            while ((count = input.read(block)) != -1) {
                if (Thread.currentThread().isInterrupted()) throw new IOException("分享准备已取消");
                output.write(block, 0, count);
                size += count;
            }
            complete = true;
            return size;
        } finally {
            if (!complete) target.delete();
        }
    }

    public static void enqueue(Context activity, Intent intent) {
        final List<Uri> uris;
        try { uris = streams(intent); }
        catch (Exception error) { message = "无法分享：" + error.getMessage(); return; }
        Context context = activity.getApplicationContext();
        synchronized (ShareQueue.class) { preparing++; }
        message = "正在准备发送到电脑的文件，请保持此页打开…";
        worker.execute(() -> {
            File job = null;
            boolean published = false;
            try {
                File outbox = TransferConfig.stateFile("outbox");
                if (!outbox.isDirectory() && !outbox.mkdirs()) throw new IOException("无法创建待发送目录，请检查文件访问权限");
                String id = UUID.randomUUID().toString().replace("-", "");
                job = new File(outbox, id);
                if (!job.mkdir()) throw new IOException("无法创建分享任务");
                JSONArray files = new JSONArray();
                for (int index = 0; index < uris.size(); index++) {
                    Uri uri = uris.get(index);
                    String name = null;
                    if ("file".equals(uri.getScheme())) {
                        File source = sharedFile(new File(uri.getPath()), Environment.getExternalStorageDirectory());
                        name = source.getName();
                    } else {
                        try (Cursor cursor = context.getContentResolver().query(uri,
                                new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
                            if (cursor != null && cursor.moveToFirst()) {
                                int column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                                if (column >= 0) name = cursor.getString(column);
                            }
                        } catch (RuntimeException ignored) { }
                        if (name == null || name.isEmpty()) {
                            String mime = context.getContentResolver().getType(uri);
                            String extension = android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(mime);
                            name = extension == null ? "shared-file" : "shared-file." + extension;
                        }
                    }
                    File target = new File(job, Integer.toString(index));
                    long size;
                    try (InputStream input = context.getContentResolver().openInputStream(uri)) { size = copyTo(input, target); }
                    files.put(new JSONObject().put("file", Integer.toString(index)).put("name", safeName(name))
                            .put("size", size).put("mtime", target.lastModified() / 1000));
                }
                JSONObject request = new JSONObject().put("version", 1).put("id", id).put("files", files);
                File temp = new File(job, "request.tmp");
                byte[] encoded = request.toString().getBytes(StandardCharsets.UTF_8);
                if (encoded.length > 512 * 1024) throw new IOException("分享文件过多或文件名过长，请分批分享");
                Files.write(temp.toPath(), encoded);
                Files.move(temp.toPath(), new File(job, "request.json").toPath(), StandardCopyOption.ATOMIC_MOVE);
                published = true;
                message = "";
            } catch (Exception error) {
                message = "分享准备失败：" + (error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
            } finally {
                if (!published && job != null) {
                    File[] staged = job.listFiles();
                    if (staged != null) for (File file : staged) if (file.isFile()) file.delete();
                    job.delete();
                }
                synchronized (ShareQueue.class) { preparing--; }
            }
        });
    }

    public static String status() {
        if (preparing > 0) return "正在准备发送到电脑的文件，请保持此页打开…";
        if (!message.isEmpty()) return message;
        File[] jobs = TransferConfig.stateFile("outbox").listFiles();
        int pending = 0;
        if (jobs != null) for (File job : jobs) {
            if (job.getName().matches("[0-9a-f]{32}") && new File(job, "request.json").isFile()
                    && !new File(job, "received").exists()) pending++;
        }
        return pending == 0 ? "" : "待完成的分享：" + pending + " 个任务。电脑打开新版互传后自动接收到 Downloads。";
    }
}
