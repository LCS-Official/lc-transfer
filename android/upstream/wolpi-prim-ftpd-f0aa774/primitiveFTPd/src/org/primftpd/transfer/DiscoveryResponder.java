/* LC 互传, 2026. Apache-2.0. */
package org.primftpd.transfer;

import android.content.Context;
import android.os.Build;
import android.os.SystemClock;
import android.net.wifi.WifiManager;
import android.util.Base64;
import org.json.JSONObject;
import org.primftpd.pojo.KeyParser;
import java.io.ByteArrayInputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.PublicKey;
import java.util.ArrayList;
import java.util.List;

/** Unicast works across overlays where broadcast/mDNS cannot travel. No secrets in discovery. */
public final class DiscoveryResponder implements AutoCloseable {
    public static volatile Request pending;
    private static volatile String dismissed = "";
    private static volatile long dismissedUntil;
    private final DatagramSocket socket;
    private volatile boolean closed;
    private final Context context;
    private final String hostFingerprint;
    private WifiManager.MulticastLock multicastLock;
    public static final class Request {
        public final String name, encoded, code;
        public final long expires = SystemClock.elapsedRealtime() + 60000;
        Request(String name, PublicKey key, String code) {
            this.name = name; this.encoded = Base64.encodeToString(key.getEncoded(), Base64.NO_WRAP); this.code = code;
        }
    }

    public DiscoveryResponder(Context context, String bindIp) throws Exception {
        this.context = context.getApplicationContext();
        hostFingerprint = TransferConfig.hostFingerprint(context);
        socket = new DatagramSocket(null);
        socket.setReuseAddress(true);
        socket.bind(new InetSocketAddress(InetAddress.getByName(bindIp == null ? "0.0.0.0" : bindIp), 52023));
        WifiManager wifi = (WifiManager)this.context.getSystemService(Context.WIFI_SERVICE);
        if (wifi != null) {
            multicastLock = wifi.createMulticastLock("LC discovery");
            multicastLock.setReferenceCounted(false);
            multicastLock.acquire();
        }
        Thread thread = new Thread(this::listen, "LC discovery");
        thread.setDaemon(true);
        thread.start();
    }

    private void listen() {
        byte[] buffer = new byte[2049];
        while (!closed) {
            try {
                DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                socket.receive(packet);
                if (packet.getLength() > 2048) continue;
                JSONObject query = new JSONObject(new String(packet.getData(), 0, packet.getLength(), StandardCharsets.UTF_8));
                if (!"lc-transfer/1".equals(query.optString("protocol"))) continue;
                String nonce = query.optString("nonce");
                if (!nonce.matches("[0-9a-f]{32}")) continue;
                String keyText = query.optString("key");
                if (!keyText.startsWith("ecdsa-sha2-nistp256 ") || keyText.length() > 300) continue;
                List<PublicKey> keys = KeyParser.parsePublicKeys(new ByteArrayInputStream(keyText.getBytes(StandardCharsets.US_ASCII)), new ArrayList<>());
                if (keys.size() != 1) continue;
                PublicKey key = keys.get(0);
                boolean paired = TransferConfig.authorized(context, "user", key);
                String code = TransferConfig.pairingCode(hostFingerprint,
                    TransferConfig.fingerprint(Base64.decode(keyText.split(" ")[1], Base64.DEFAULT)));
                String name = query.optString("name", "电脑").replaceAll("[\\p{Cntrl}]", "");
                if (name.length() > 80) name = name.substring(0, 80);
                Request request = new Request(name, key, code);
                long now = SystemClock.elapsedRealtime();
                // A paired phone stays quiet until its owner chooses "忘记电脑".
                if (!paired && TransferConfig.prefs(context).getString(TransferConfig.PC_KEY, "").isEmpty()
                        && (!request.encoded.equals(dismissed) || now > dismissedUntil)
                        && (pending == null || pending.expires < now)) pending = request;
                JSONObject answer = new JSONObject().put("protocol", "lc-transfer/1").put("nonce", nonce)
                    .put("name", Build.MODEL).put("fingerprint", hostFingerprint).put("port", 52022).put("paired", paired);
                byte[] data = answer.toString().getBytes(StandardCharsets.UTF_8);
                socket.send(new DatagramPacket(data, data.length, packet.getAddress(), packet.getPort()));
            } catch (Exception ex) {
                // Invalid datagrams cannot stop discovery; closing the socket exits the loop.
            }
        }
    }

    public static void decide(Context context, Request request, boolean allow) {
        if (allow) TransferConfig.prefs(context).edit().putString(TransferConfig.PC_KEY, request.encoded)
            .putString(TransferConfig.PC_NAME, request.name).commit();
        else { dismissed = request.encoded; dismissedUntil = SystemClock.elapsedRealtime() + 60000; }
        if (pending == request) pending = null;
    }
    @Override public synchronized void close() {
        closed = true; socket.close(); pending = null;
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
    }
}
