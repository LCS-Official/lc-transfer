/* LC 互传 simplified phone UI, 2026. Apache-2.0. */
package org.primftpd.ui;

import android.Manifest;
import android.content.ComponentName;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import org.primftpd.R;
import org.primftpd.prefs.LoadPrefsUtil;
import org.primftpd.services.SshServerService;
import org.primftpd.transfer.DiscoveryResponder;
import org.primftpd.transfer.TransferConfig;
import org.primftpd.transfer.ShareQueue;
import org.primftpd.util.ServicesStartStopUtil;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One screen: connection settings while idle, transfer list while connected. */
public class MainTabsActivity extends AppCompatActivity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private ScrollView settingsScroll;
    private FrameLayout transfers;
    private TextView connection, description, shareStatus;
    private EditText bind, directory;
    private Button action, openApp, save, forget;
    private boolean paused, preparing, shareHandled;
    private long retryAt;
    private AlertDialog pairDialog;
    private String failure = "";
    private final Runnable tick = new Runnable() {
        @Override public void run() { update(); handler.postDelayed(this, 1000); }
    };

    private int dp(int n) { return (int)(n * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value); view.setTextSize(size); view.setTextColor(Color.rgb(35, 54, 62));
        view.setPadding(0, dp(5), 0, dp(5));
        if (bold) view.setTypeface(null, android.graphics.Typeface.BOLD);
        return view;
    }
    private Button button(String value) {
        Button view = new Button(this); view.setText(value); view.setAllCaps(false);
        view.setTextColor(Color.rgb(35, 151, 198)); view.setMinHeight(dp(48)); return view;
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        paused = state != null && state.getBoolean("paused", false);
        shareHandled = state != null && state.getBoolean("shareHandled", false);
        if (ShareQueue.accepts(getIntent()) && !shareHandled) paused = false;
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        LinearLayout outer = new LinearLayout(this);
        outer.setOrientation(LinearLayout.VERTICAL); outer.setBackgroundColor(Color.WHITE);
        outer.setPadding(dp(20), dp(12), dp(20), dp(12));
        ViewCompat.setOnApplyWindowInsetsListener(outer, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(dp(20) + bars.left, dp(12) + bars.top, dp(20) + bars.right, dp(12) + bars.bottom);
            return insets;
        });
        setContentView(outer);
        outer.addView(text("互传", 28, true));
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL); card.setPadding(dp(18), dp(15), dp(18), dp(15));
        GradientDrawable background = new GradientDrawable();
        background.setColor(Color.rgb(242, 248, 251)); background.setCornerRadius(dp(20)); card.setBackground(background);
        connection = text("等待连接", 20, true); description = text("电脑打开互传后会自动发现手机。", 14, false);
        card.addView(connection); card.addView(description);
        shareStatus = text("", 14, false); shareStatus.setVisibility(View.GONE);
        card.addView(shareStatus); outer.addView(card);

        settingsScroll = new ScrollView(this);
        LinearLayout config = new LinearLayout(this); config.setOrientation(LinearLayout.VERTICAL);
        config.setPadding(0, dp(14), 0, dp(8)); settingsScroll.addView(config);
        SharedPreferences prefs = TransferConfig.prefs(this);
        TransferConfig.prepareNetwork(this);
        config.addView(text("绑定 IP", 14, true));
        bind = new EditText(this); bind.setSingleLine(true);
        bind.setInputType(android.text.InputType.TYPE_CLASS_PHONE);
        bind.setText(prefs.getString(LoadPrefsUtil.PREF_KEY_BIND_IP, "0.0.0.0")); config.addView(bind);
        config.addView(text("默认 0.0.0.0：支持局域网和虚拟网络。填写具体 IP 可限制监听接口。", 12, false));
        config.addView(text("文件保存到", 14, true));
        directory = new EditText(this); directory.setText(TransferConfig.receiveDir(this));
        directory.setSingleLine(false); directory.setSelectAllOnFocus(false); config.addView(directory);
        config.addView(text("打开文件使用", 14, true));
        openApp = button(TransferFragment.openAppLabel(this)); openApp.setOnClickListener(v -> chooseApp()); config.addView(openApp);
        save = button("保存设置"); save.setOnClickListener(v -> saveSettings()); config.addView(save);
        forget = button("忘记已配对的电脑");
        forget.setOnClickListener(v -> new AlertDialog.Builder(this).setTitle("忘记电脑？")
            .setMessage("下次连接需要重新在手机上确认。已有文件和传输记录会保留。")
            .setNegativeButton("取消", null).setPositiveButton("忘记", (d, w) -> {
                TransferConfig.prefs(this).edit().remove(TransferConfig.PC_KEY).remove(TransferConfig.PC_NAME).commit();
                ServicesStartStopUtil.stopServers(this); retryAt = SystemClock.elapsedRealtime() + 1500; update();
            }).show());
        config.addView(forget);
        outer.addView(settingsScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        transfers = new FrameLayout(this); transfers.setId(R.id.view_pager);
        outer.addView(transfers, new LinearLayout.LayoutParams(-1, 0, 1));
        if (state == null) getSupportFragmentManager().beginTransaction().replace(transfers.getId(), new TransferFragment(), "transfers").commit();
        action = button("授权文件访问");
        action.setOnClickListener(v -> {
            if (!hasStorage()) { requestStorage(); return; }
            paused = !paused;
            if (paused) ServicesStartStopUtil.stopServers(this);
            else { retryAt = 0; failure = ""; }
            update();
        });
        outer.addView(action, new LinearLayout.LayoutParams(-1, dp(60)));
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 11);
    }

    @Override protected void onResume() { super.onResume(); handler.post(tick); }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putBoolean("paused", paused); state.putBoolean("shareHandled", shareHandled); super.onSaveInstanceState(state);
    }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent); setIntent(intent); shareHandled = false;
        if (ShareQueue.accepts(intent)) { paused = false; retryAt = 0; failure = ""; }
        update();
    }
    @Override protected void onPause() { handler.removeCallbacks(tick); super.onPause(); }
    @Override protected void onDestroy() { handler.removeCallbacks(tick); if (pairDialog != null) pairDialog.dismiss(); super.onDestroy(); }
    private boolean hasStorage() {
        return Build.VERSION.SDK_INT >= 30 ? Environment.isExternalStorageManager()
            : checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
    }
    private void requestStorage() {
        if (Build.VERSION.SDK_INT >= 30) startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
            Uri.parse("package:" + getPackageName())));
        else requestPermissions(new String[] {Manifest.permission.WRITE_EXTERNAL_STORAGE}, 12);
    }
    private boolean addressReady(String ip) {
        try { return "0.0.0.0".equals(ip) || NetworkInterface.getByInetAddress(InetAddress.getByName(ip)) != null; }
        catch (Exception ex) { return false; }
    }
    private void update() {
        if (isFinishing()) return;
        if (!shareHandled && ShareQueue.accepts(getIntent()) && hasStorage()) {
            shareHandled = true;
            ShareQueue.enqueue(this, getIntent());
            androidx.fragment.app.Fragment fragment = getSupportFragmentManager().findFragmentByTag("transfers");
            if (fragment instanceof TransferFragment) ((TransferFragment)fragment).showSent();
        }
        String sharing = !shareHandled && ShareQueue.accepts(getIntent())
                ? "要发送分享文件，请先授权文件访问。" : ShareQueue.status();
        shareStatus.setText(sharing); shareStatus.setVisibility(sharing.isEmpty() ? View.GONE : View.VISIBLE);
        boolean connected = SshServerService.connected();
        if (connected && settingsScroll.getVisibility() != View.GONE) {
            bind.clearFocus(); directory.clearFocus();
            android.view.inputmethod.InputMethodManager keyboard = (android.view.inputmethod.InputMethodManager)getSystemService(INPUT_METHOD_SERVICE);
            keyboard.hideSoftInputFromWindow(settingsScroll.getWindowToken(), 0);
        }
        settingsScroll.setVisibility(connected ? View.GONE : View.VISIBLE);
        transfers.setVisibility(connected ? View.VISIBLE : View.GONE);
        forget.setVisibility(TransferConfig.prefs(this).contains(TransferConfig.PC_KEY) ? View.VISIBLE : View.GONE);
        save.setEnabled(hasStorage() && !preparing);
        String ip = TransferConfig.prefs(this).getString(LoadPrefsUtil.PREF_KEY_BIND_IP, "0.0.0.0");
        if (!hasStorage()) {
            connection.setText("需要文件访问权限"); description.setText("授权后，电脑可浏览手机共享存储，并向手机传输文件。");
            action.setText("授权文件访问"); return;
        }
        action.setText(paused ? "允许电脑连接" : connected ? "断开连接" : "暂停连接");
        if (connected) {
            connection.setText(TransferConfig.prefs(this).getString(TransferConfig.PC_NAME, "电脑") + " · 已连接");
            description.setText("保存到 " + TransferConfig.receiveDir(this));
        } else if (paused) { connection.setText("连接已暂停"); description.setText("点击下方按钮后，电脑可再次自动连接。"); }
        else if (!addressReady(ip)) { connection.setText("等待绑定 IP"); description.setText("当前没有 " + ip + "，请打开虚拟网络或修改绑定 IP。"); }
        else {
            connection.setText(SshServerService.ready() ? "等待电脑连接" : "准备连接…");
            String error = failure.isEmpty() ? SshServerService.statusError : failure;
            description.setText(error.isEmpty() ? "电脑自动发现 · 局域网优先，虚拟网络备用 · " + ip : error);
            if (!preparing && !SshServerService.ready() && SystemClock.elapsedRealtime() > retryAt) start();
        }
        DiscoveryResponder.Request request = DiscoveryResponder.pending;
        if (!paused && request != null && request.expires > SystemClock.elapsedRealtime() && pairDialog == null) {
            pairDialog = new AlertDialog.Builder(this).setTitle("允许这台电脑连接？")
                .setMessage(request.name + "\n\n核对码：" + request.code + "\n确认与电脑显示的一致。允许后，电脑可浏览共享文件并双向传输。")
                .setNegativeButton("拒绝", (d, w) -> DiscoveryResponder.decide(this, request, false))
                .setPositiveButton("允许", (d, w) -> DiscoveryResponder.decide(this, request, true))
                .setOnCancelListener(d -> DiscoveryResponder.decide(this, request, false)).create();
            pairDialog.setOnDismissListener(d -> pairDialog = null); pairDialog.show();
        }
    }
    private void start() {
        preparing = true; retryAt = SystemClock.elapsedRealtime() + 10000;
        new Thread(() -> {
            try {
                TransferConfig.prepare(getApplicationContext());
                handler.post(() -> {
                    preparing = false;
                    if (!paused && !isFinishing()) ServicesStartStopUtil.startServers(this);
                    update();
                });
            } catch (Exception ex) { handler.post(() -> { preparing = false; failure = "无法启动：" + ex.getMessage(); update(); }); }
        }, "LC prepare").start();
    }
    private void saveSettings() {
        try {
            String ip = bind.getText().toString().trim();
            if (!ip.matches("(?:\\d{1,3}\\.){3}\\d{1,3}")) throw new IllegalArgumentException("绑定 IP 需填写 IPv4 地址");
            for (String part : ip.split("\\.")) if (Integer.parseInt(part) > 255) throw new IllegalArgumentException("IP 地址无效");
            String path = TransferConfig.validateReceive(directory.getText().toString().trim()).getPath();
            TransferConfig.prefs(this).edit().putString(LoadPrefsUtil.PREF_KEY_BIND_IP, ip)
                .putBoolean("lc_simple_v2", true).putString(TransferConfig.RECEIVE, path).commit();
            TransferConfig.writeDevice(this);
            ServicesStartStopUtil.stopServers(this); retryAt = SystemClock.elapsedRealtime() + 2000; failure = "";
            Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show(); update();
        } catch (Exception ex) { Toast.makeText(this, ex.getMessage(), Toast.LENGTH_LONG).show(); }
    }
    private void chooseApp() {
        Map<String, String> options = new LinkedHashMap<>();
        options.put("", "每次使用系统选择");
        try { getPackageManager().getPackageInfo("bin.mt.plus", 0); options.put("bin.mt.plus/bin.mt.plus.OpenFileActivity", "MT 管理器"); }
        catch (PackageManager.NameNotFoundException ignored) { }
        for (String mime : new String[] {"application/zip", "application/x-7z-compressed", "application/octet-stream"}) {
            Intent intent = new Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse("content://" + getPackageName() + ".provider/selection.zip"), mime);
            for (ResolveInfo info : getPackageManager().queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)) {
                ComponentName component = new ComponentName(info.activityInfo.packageName, info.activityInfo.name);
                if (!"bin.mt.plus".equals(info.activityInfo.packageName)) options.put(component.flattenToString(), info.loadLabel(getPackageManager()).toString());
            }
        }
        List<String> keys = new ArrayList<>(options.keySet());
        int selected = Math.max(0, keys.indexOf(TransferConfig.prefs(this).getString(TransferConfig.OPEN_APP, "")));
        new AlertDialog.Builder(this).setTitle("打开文件使用的应用")
            .setSingleChoiceItems(options.values().toArray(new String[0]), selected, (dialog, which) -> {
                TransferConfig.prefs(this).edit().putString(TransferConfig.OPEN_APP, keys.get(which)).commit();
                openApp.setText(TransferFragment.openAppLabel(this)); dialog.dismiss();
            }).setNegativeButton("取消", null).show();
    }
}
