/* LC 互传 modifications, 2026. Based on Primitive FTPd, Apache-2.0. */
package org.primftpd.ui;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.ComponentName;
import android.content.Context;
import android.webkit.MimeTypeMap;
import android.net.Uri;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.core.content.FileProvider;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.primftpd.transfer.TransferConfig;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/** Native transfer history; the PC mirrors progress over the existing SFTP channel. */
public class TransferFragment extends Fragment {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final List<Row> rows = new ArrayList<>();
    private TextView status;
    private RowsAdapter adapter;
    private String lastJson = "";
    private boolean receivedOnly = true;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            load();
            handler.postDelayed(this, 1000);
        }
    };

    @Override public View onCreateView(@NonNull LayoutInflater inflater, ViewGroup container, Bundle state) {
        if (org.primftpd.transfer.ShareQueue.accepts(requireActivity().getIntent())) receivedOnly = false;
        LinearLayout outer = new LinearLayout(requireContext());
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.setPadding(0, dp(12), 0, dp(8));
        outer.setBackgroundColor(Color.WHITE);
        status = text("与电脑之间的文件传输会显示在这里。", 13, false);
        outer.addView(status);
        LinearLayout filters = new LinearLayout(requireContext());
        Button all = new Button(requireContext());
        all.setText("已传到电脑");
        all.setOnClickListener(v -> { receivedOnly = false; lastJson = ""; load(); });
        filters.addView(all, new LinearLayout.LayoutParams(0, dp(52), 1));
        Button received = new Button(requireContext());
        received.setText("接收到手机");
        received.setOnClickListener(v -> { receivedOnly = true; lastJson = ""; load(); });
        filters.addView(received, new LinearLayout.LayoutParams(0, dp(52), 1));
        outer.addView(filters);
        RecyclerView list = new RecyclerView(requireContext());
        list.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new RowsAdapter();
        list.setAdapter(adapter);
        list.setItemAnimator(null);
        outer.addView(list, new LinearLayout.LayoutParams(-1, 0, 1));
        return outer;
    }

    @Override public void onResume() { super.onResume(); handler.post(refresh); }
    @Override public void onPause() { handler.removeCallbacks(refresh); super.onPause(); }
    @Override public void onDestroyView() {
        handler.removeCallbacks(refresh);
        adapter = null;
        rows.clear();
        lastJson = "";
        super.onDestroyView();
    }

    private int dp(int n) { return (int)(n * getResources().getDisplayMetrics().density); }

    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(requireContext());
        view.setText(value);
        view.setTextColor(Color.rgb(36, 62, 63));
        view.setTextSize(size);
        if (bold) view.setTypeface(null, android.graphics.Typeface.BOLD);
        view.setPadding(0, dp(4), 0, dp(4));
        return view;
    }

    private String label(String state) {
        switch (state) {
            case "running": return "正在传输";
            case "done": return "已完成";
            case "packed": return "已写入 ZIP，等待归档完成";
            case "discarded": return "ZIP 未完成，未保存";
            case "error": return "失败";
            case "cancelled": return "已取消";
            case "interrupted": return "已中断";
            default: return state;
        }
    }

    private String size(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024L * 1024) return String.format(Locale.CHINA, "%.1f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) return String.format(Locale.CHINA, "%.1f MB", bytes / 1048576.0);
        return String.format(Locale.CHINA, "%.2f GB", bytes / 1073741824.0);
    }

    private static class Row {
        final String title, detail, path;
        final boolean header, running;
        final int progress;
        Row(String title, String detail, String path, boolean header, boolean running, int progress) {
            this.title = title; this.detail = detail; this.path = path;
            this.header = header; this.running = running; this.progress = progress;
        }
    }

    private class Holder extends RecyclerView.ViewHolder {
        final TextView title, detail;
        final ProgressBar progress;
        final Button view;
        Holder(LinearLayout root) {
            super(root);
            title = text("", 15, false);
            detail = text("", 12, false);
            progress = new ProgressBar(requireContext(), null, android.R.attr.progressBarStyleHorizontal);
            progress.setMax(1000);
            root.addView(title); root.addView(detail);
            root.addView(progress, new LinearLayout.LayoutParams(-1, dp(10)));
            view = new Button(requireContext());
            view.setText("View · 打开文件");
            root.addView(view, new LinearLayout.LayoutParams(-1, dp(48)));
        }
    }

    private class RowsAdapter extends RecyclerView.Adapter<Holder> {
        @NonNull @Override public Holder onCreateViewHolder(@NonNull ViewGroup parent, int type) {
            LinearLayout root = new LinearLayout(parent.getContext());
            root.setOrientation(LinearLayout.VERTICAL);
            root.setPadding(dp(12), dp(8), dp(12), dp(8));
            root.setLayoutParams(new RecyclerView.LayoutParams(-1, -2));
            return new Holder(root);
        }
        @Override public void onBindViewHolder(@NonNull Holder holder, int position) {
            Row row = rows.get(position);
            holder.title.setText(row.title);
            holder.title.setTextSize(row.header ? 18 : 15);
            holder.title.setTypeface(null, row.header ? android.graphics.Typeface.BOLD : android.graphics.Typeface.NORMAL);
            holder.detail.setText(row.detail);
            holder.detail.setTextColor(row.path.isEmpty() ? Color.DKGRAY : Color.rgb(20, 133, 123));
            holder.itemView.setBackgroundColor(row.header ? Color.rgb(224, 241, 238) : Color.WHITE);
            holder.progress.setVisibility(row.running ? View.VISIBLE : View.GONE);
            holder.progress.setProgress(row.progress);
            holder.view.setVisibility(row.path.isEmpty() ? View.GONE : View.VISIBLE);
            holder.view.setOnClickListener(row.path.isEmpty() ? null : v -> viewReceived(row.path));
            holder.itemView.setOnClickListener(null);
        }
        @Override public int getItemCount() { return rows.size(); }
    }

    public void showSent() { receivedOnly = false; lastJson = ""; load(); }

    private void load() {
        if (adapter == null || !isAdded()) return;
        File file = new File(Environment.getExternalStorageDirectory(), ".lc-transfer/history.json");
        if (!file.exists()) {
            status.setText("暂无传输记录，在电脑选择文件即可开始。");
            return;
        }
        try {
            if (file.length() > 16 * 1024 * 1024) {
                status.setText("传输记录超过 16 MB，完整列表可在电脑端查看。");
                return;
            }
            String json = new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
            if (json.equals(lastJson)) return;
            JSONObject data = new JSONObject(json);
            JSONArray jobs = data.optJSONArray("jobs");
            long updated = (long)(data.optDouble("updated") * 1000);
            boolean running = false;
            if (jobs != null) for (int i = 0; i < jobs.length(); i++) {
                running |= "running".equals(jobs.getJSONObject(i).optString("state"));
            }
            boolean stale = running && System.currentTimeMillis() - updated > 15000;
            status.setText("上次同步：" + new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA).format(new Date(updated)) +
                (stale ? "\n超过 15 秒未更新，正在进行的任务状态可能已过期。" : ""));
            if (json.equals(lastJson)) return;
            List<Row> next = new ArrayList<>();
            if (jobs != null) for (int i = 0; i < jobs.length(); i++) {
                JSONObject job = jobs.getJSONObject(i);
                boolean receiving = "upload".equals(job.optString("direction"));
                if (receivedOnly != receiving) continue;
                String target = job.optString("archive");
                if (target.isEmpty()) target = job.optString("target");
                String date = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA)
                    .format(new Date((long)(job.optDouble("started") * 1000)));
                String direction = receiving ? "电脑 → 手机" : "手机 → 电脑";
                next.add(new Row(date, direction + " · " + label(job.optString("state")) +
                    (job.optString("error").isEmpty() ? "" : "\n" + job.optString("error")), "", true, false, 0));
                JSONArray files = job.optJSONArray("files");
                if (files == null || files.length() == 0) {
                    next.add(new Row("等待 / 正在准备", String.valueOf(job.optJSONArray("selected")), "", false, false, 0));
                    continue;
                }
                for (int j = 0; j < files.length(); j++) {
                    JSONObject entry = files.getJSONObject(j);
                    String state = entry.optString("state");
                    long done = entry.optLong("done"), total = entry.optLong("size");
                    String path = receiving && "done".equals(state) ? entry.optString("target") : "";
                    String detail = label(state) + " · " + size(done) + " / " + size(total);
                    if (!path.isEmpty()) detail += "\n保存位置：" + path;
                    next.add(new Row(entry.optString("name"), detail, path, false, "running".equals(state),
                        total > 0 ? (int)(Math.min(1.0, (double)done / total) * 1000) : 0));
                }
            }
            if (next.isEmpty()) next.add(new Row(receivedOnly ? "尚无电脑传来的文件" : "尚无传到电脑的文件", "", "", false, false, 0));
            rows.clear(); rows.addAll(next);
            adapter.notifyDataSetChanged();
            lastJson = json;
        } catch (Exception ex) {
            // Keep the last valid view if the PC is currently updating the status file.
            status.setText("等待传输状态更新…如一直为空，请检查文件访问权限。");
        }
    }

    public static String openAppLabel(Context context) {
        String saved = TransferConfig.prefs(context).getString(TransferConfig.OPEN_APP, "");
        if (saved.isEmpty()) return "每次使用系统选择";
        ComponentName component = ComponentName.unflattenFromString(saved);
        if (component == null) return "每次使用系统选择";
        if ("bin.mt.plus".equals(component.getPackageName())) return "MT 管理器";
        try { return context.getPackageManager().getActivityInfo(component, 0).loadLabel(context.getPackageManager()).toString(); }
        catch (Exception ex) { return "所选应用不可用 · 点击重新选择"; }
    }

    private void viewReceived(String path) {
        try {
            File file = new File(path).getCanonicalFile();
            String root = Environment.getExternalStorageDirectory().getCanonicalPath() + File.separator;
            if (!file.getPath().startsWith(root) || !file.isFile()) {
                Toast.makeText(requireContext(), "文件不存在或不在共享存储中", Toast.LENGTH_LONG).show();
                return;
            }
            Uri uri = FileProvider.getUriForFile(requireContext(), requireContext().getPackageName() + ".provider", file);
            String extension = file.getName().contains(".") ? file.getName().substring(file.getName().lastIndexOf('.') + 1).toLowerCase(Locale.ROOT) : "";
            String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
            if ("7z".equals(extension)) mime = "application/x-7z-compressed";
            if (mime == null) mime = "application/octet-stream";
            Intent open = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            ComponentName preferred = ComponentName.unflattenFromString(TransferConfig.prefs(requireContext()).getString(TransferConfig.OPEN_APP, ""));
            if (preferred != null) {
                open.setComponent(preferred);
                if ("bin.mt.plus".equals(preferred.getPackageName())) open.setDataAndType(uri, "application/octet-stream");
                try { startActivity(open); return; }
                catch (ActivityNotFoundException | SecurityException ex) {
                    Toast.makeText(requireContext(), "所选应用无法打开此文件，改用系统选择", Toast.LENGTH_SHORT).show();
                }
                open.setComponent(null); open.setDataAndType(uri, mime);
            }
            try {
                if (open.resolveActivity(requireContext().getPackageManager()) == null) throw new ActivityNotFoundException();
                startActivity(Intent.createChooser(open, "打开 " + file.getName()));
                return;
            } catch (ActivityNotFoundException | SecurityException ex) {
                // Directory navigation remains useful when no app accepts this file type.
            }
            String relative = file.getParentFile().getCanonicalPath().substring(root.length() - 1);
            if (relative.startsWith("/")) relative = relative.substring(1);
            Uri directory = DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:" + relative);
            Intent browse = new Intent(Intent.ACTION_VIEW);
            browse.setDataAndType(directory, DocumentsContract.Document.MIME_TYPE_DIR);
            try {
                startActivity(browse);
            } catch (ActivityNotFoundException | SecurityException ex) {
                Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                picker.addCategory(Intent.CATEGORY_OPENABLE);
                picker.setType("*/*");
                picker.putExtra(DocumentsContract.EXTRA_INITIAL_URI, directory);
                startActivity(picker);
            }
        } catch (ActivityNotFoundException ex) {
            Toast.makeText(requireContext(), "手机没有可用文件管理器，请安装 MT 或启用系统文件应用", Toast.LENGTH_LONG).show();
        } catch (Exception ex) {
            Toast.makeText(requireContext(), "无法打开文件：" + ex.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
}
