/* LC 互传 phone-side media listing and small previews, 2026. Apache-2.0. */
package org.primftpd.transfer;

import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.ThumbnailUtils;
import android.os.Build;
import android.provider.MediaStore;
import android.util.JsonWriter;
import android.util.Size;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashSet;
import java.util.Set;
import java.util.zip.GZIPOutputStream;
import java.util.regex.Pattern;

/** Generated files are exposed read-only through the existing authenticated SFTP session. */
public final class MediaCatalog {
    public static final int PREVIEW_EDGE = 320;
    private static final int LIMIT = 100000;
    private static final Pattern HIDDEN = Pattern.compile("(^|/)\\.");
    private static final Pattern VIDEO = Pattern.compile(".*\\.(mp4|mkv|mov|avi|webm|3gp|m4v|ts)$", Pattern.CASE_INSENSITIVE);
    private static long catalogAt;
    private static int previewsCreated;
    private MediaCatalog() { }

    private static File cacheDir(Context context) throws IOException {
        File dir = new File(context.getCacheDir(), "media-previews");
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("Cannot create preview cache");
        return dir;
    }

    public static synchronized File catalog(Context context, File root) throws IOException {
        File output = new File(context.getCacheDir(), "media-catalog.json.gz");
        long now = System.currentTimeMillis();
        if (output.isFile() && now - catalogAt < 5000) return output;
        File temp = new File(context.getCacheDir(), "media-catalog.tmp");
        String prefix = root.getCanonicalPath() + "/";
        String[] projection = {MediaStore.Files.FileColumns.DATA, MediaStore.Files.FileColumns.SIZE,
            MediaStore.Files.FileColumns.DATE_MODIFIED};
        Set<String> seen = new HashSet<>();
        int count = 0;
        boolean truncated = false;
        try (Cursor cursor = context.getContentResolver().query(MediaStore.Files.getContentUri("external"), projection,
                MediaStore.Files.FileColumns.MIME_TYPE + " IS NOT NULL", null,
                MediaStore.Files.FileColumns.DATE_MODIFIED + " DESC");
             JsonWriter json = new JsonWriter(new OutputStreamWriter(new GZIPOutputStream(new FileOutputStream(temp)), StandardCharsets.UTF_8))) {
            if (cursor == null) throw new IOException("MediaStore is unavailable");
            json.beginObject().name("version").value(1).name("source").value("MediaStore").name("files").beginArray();
            while (cursor.moveToNext()) {
                String path = cursor.getString(0);
                if (path == null || !path.startsWith(prefix)) continue;
                String relative = path.substring(prefix.length());
                if (relative.isEmpty() || relative.contains("/../") || relative.startsWith("../")
                        || HIDDEN.matcher(relative).find()
                        || relative.startsWith("Android/data/") || relative.startsWith("Android/obb/")
                        || !seen.add(relative)) continue;
                if (count >= LIMIT) { truncated = true; break; }
                json.beginArray().value(relative).value(Math.max(0, cursor.getLong(1)))
                    .value(Math.max(0, cursor.getLong(2))).endArray();
                count++;
            }
            json.endArray().name("truncated").value(truncated).name("generated").value(now / 1000).endObject();
        } catch (Exception ex) {
            temp.delete();
            throw new IOException("Cannot read phone media catalog", ex);
        }
        Files.move(temp.toPath(), output.toPath(), StandardCopyOption.REPLACE_EXISTING);
        catalogAt = System.currentTimeMillis();
        return output;
    }

    public static synchronized File preview(Context context, File root, String relative) throws IOException {
        File source = new File(root, relative).getCanonicalFile();
        String prefix = root.getCanonicalPath() + "/";
        if (!source.getPath().startsWith(prefix) || !source.isFile()
                || relative.startsWith(".lc-transfer/") || relative.startsWith("Android/data/") || relative.startsWith("Android/obb/"))
            throw new IOException("Preview source is outside shared storage or unavailable");
        String token = source.getPath() + "\n" + source.length() + "\n" + source.lastModified();
        StringBuilder name = new StringBuilder();
        try {
            for (byte item : MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8)))
                name.append(String.format(java.util.Locale.ROOT, "%02x", item & 255));
        } catch (java.security.NoSuchAlgorithmException ex) { throw new IOException(ex); }
        File folder = cacheDir(context);
        File output = new File(folder, name + ".jpg");
        if (output.isFile()) { output.setLastModified(System.currentTimeMillis()); return output; }
        Bitmap bitmap = null;
        File temp = new File(folder, name + ".tmp");
        try {
            boolean video = VIDEO.matcher(source.getName()).matches();
            if (Build.VERSION.SDK_INT >= 29) {
                Size size = new Size(PREVIEW_EDGE, PREVIEW_EDGE);
                bitmap = video ? ThumbnailUtils.createVideoThumbnail(source, size, null)
                    : ThumbnailUtils.createImageThumbnail(source, size, null);
            } else if (video) {
                bitmap = ThumbnailUtils.createVideoThumbnail(source.getPath(), MediaStore.Video.Thumbnails.MINI_KIND);
            } else {
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inJustDecodeBounds = true;
                BitmapFactory.decodeFile(source.getPath(), options);
                int size = Math.max(options.outWidth, options.outHeight);
                options.inSampleSize = 1;
                while (size / options.inSampleSize > PREVIEW_EDGE) options.inSampleSize *= 2;
                options.inJustDecodeBounds = false;
                bitmap = BitmapFactory.decodeFile(source.getPath(), options);
            }
            if (bitmap == null) throw new IOException("Unsupported preview format");
            int edge = Math.max(bitmap.getWidth(), bitmap.getHeight());
            if (edge > PREVIEW_EDGE) {
                Bitmap small = Bitmap.createScaledBitmap(bitmap, Math.max(1, bitmap.getWidth() * PREVIEW_EDGE / edge),
                    Math.max(1, bitmap.getHeight() * PREVIEW_EDGE / edge), true);
                if (small != bitmap) bitmap.recycle();
                bitmap = small;
            }
            try (FileOutputStream stream = new FileOutputStream(temp)) {
                if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 55, stream)) throw new IOException("Cannot encode preview");
            }
            if (temp.length() > 96 * 1024) throw new IOException("Preview exceeds size limit");
            Files.move(temp.toPath(), output.toPath(), StandardCopyOption.REPLACE_EXISTING);
            if (++previewsCreated % 64 == 0) trim(folder);
            return output;
        } catch (Exception ex) {
            temp.delete();
            throw new IOException("Cannot generate preview", ex);
        } finally { if (bitmap != null) bitmap.recycle(); }
    }

    private static void trim(File folder) {
        File[] files = folder.listFiles((dir, name) -> name.endsWith(".jpg"));
        if (files == null) return;
        long size = 0;
        for (File file : files) size += file.length();
        if (size <= 64L * 1024 * 1024) return;
        Arrays.sort(files, Comparator.comparingLong(File::lastModified));
        for (File file : files) {
            long length = file.length();
            if (file.delete()) size -= length;
            if (size <= 48L * 1024 * 1024) break;
        }
    }
}
