/* Runs the APK's actual SSH, key parser, Ed25519 signer and pairing helpers on the JVM. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import org.apache.sshd.SshServer;
import org.apache.sshd.common.keyprovider.AbstractKeyPairProvider;
import org.apache.sshd.common.io.mina.MinaServiceFactoryFactory;
import org.apache.sshd.common.file.nativefs.NativeFileSystemView;
import org.apache.sshd.server.sftp.SftpSubsystem;
import org.bouncycastle.jce.provider.BouncyCastleProvider;
import org.primftpd.crypto.HostKeyAlgorithm;
import org.primftpd.crypto.SignatureEd25519;
import org.primftpd.pojo.KeyParser;
import org.primftpd.transfer.TransferConfig;

public class AndroidBridgeCheck {
    public static void main(String[] args) throws Exception {
        Security.removeProvider("BC");
        Security.insertProviderAt(new BouncyCastleProvider(), 1);
        String pcText = Files.readString(Paths.get(args[0]));
        PublicKey pcKey = KeyParser.parseKeyLine(pcText);
        String approved = Base64.getEncoder().encodeToString(pcKey.getEncoded());
        if (!TransferConfig.matchesEncoded(approved, pcKey) || TransferConfig.matchesEncoded("", pcKey))
            throw new AssertionError("approval must be explicit");
        if (!"761865".equals(TransferConfig.pairingCode("phone", "computer"))) throw new AssertionError("pairing code mismatch");
        Path root = Paths.get(args[1]);
        byte[] sharedBytes = new byte[2 * 1024 * 1024 + 37];
        new Random(17).nextBytes(sharedBytes);
        File staged = root.resolve("shared-stage").toFile();
        long copied = org.primftpd.transfer.ShareQueue.copyTo(new ByteArrayInputStream(sharedBytes), staged);
        if (copied != sharedBytes.length || !Arrays.equals(sharedBytes, Files.readAllBytes(staged.toPath())))
            throw new AssertionError("Shared provider streams must copy completely");
        if (!org.primftpd.transfer.ShareQueue.safeName("../bad\\name.apk").equals(".._bad_name.apk"))
            throw new AssertionError("Shared filenames must not contain path separators");
        if (!org.primftpd.transfer.ShareQueue.sharedFile(staged, root.toFile()).equals(staged.getCanonicalFile()))
            throw new AssertionError("Shared storage file must be allowed");
        try {
            org.primftpd.transfer.ShareQueue.sharedFile(root.resolve("../outside").toFile(), root.toFile());
            throw new AssertionError("Files outside shared storage must be rejected");
        } catch (IOException expected) { }
        File failed = root.resolve("failed-stage").toFile();
        try {
            org.primftpd.transfer.ShareQueue.copyTo(new InputStream() {
                @Override public int read() throws IOException { throw new IOException("provider revoked permission"); }
            }, failed);
            throw new AssertionError("Provider failures must propagate");
        } catch (IOException expected) { }
        if (failed.exists()) throw new AssertionError("Failed staging must remove its partial copy");
        Files.delete(staged.toPath());
        HostKeyAlgorithm algo = HostKeyAlgorithm.ED_25519;
        Path pub = root.resolve("host.pub"), priv = root.resolve("host.private");
        try (FileOutputStream pubOut = new FileOutputStream(pub.toFile()); FileOutputStream privOut = new FileOutputStream(priv.toFile())) {
            algo.generateKey(pubOut, privOut);
        }
        KeyPair pair;
        try (FileInputStream pubIn = new FileInputStream(pub.toFile()); FileInputStream privIn = new FileInputStream(priv.toFile())) {
            pair = new KeyPair(algo.readPublicKey(pubIn), algo.readPrivateKey(privIn));
        }
        SshServer server = SshServer.setUpDefaultServer();
        server.getProperties().put(org.apache.sshd.common.FactoryManager.TCP_NODELAY, "true");
        server.getProperties().put(org.apache.sshd.common.FactoryManager.WINDOW_SIZE, "8388608");
        server.getProperties().put(org.apache.sshd.common.FactoryManager.SOCKET_SNDBUF, "2097152");
        server.getProperties().put(org.apache.sshd.common.FactoryManager.SOCKET_RCVBUF, "2097152");
        server.getProperties().put(org.apache.sshd.common.FactoryManager.SOCKET_LINGER, "0");
        org.apache.mina.transport.socket.DefaultSocketSessionConfig config = new org.apache.mina.transport.socket.DefaultSocketSessionConfig();
        config.setTcpNoDelay(false);
        new org.apache.sshd.common.io.mina.MinaService(server, null, null) {
            @Override protected org.apache.mina.core.service.IoService getIoService() { return null; }
            { configure(config); }
        };
        if (!config.isTcpNoDelay() || config.getSendBufferSize() != 2097152 || config.getReceiveBufferSize() != 2097152)
            throw new AssertionError("TCP_NODELAY and socket buffers must honor their own properties");
        server.setHost("127.0.0.1"); server.setPort(0);
        server.setIoServiceFactoryFactory(new MinaServiceFactoryFactory());
        server.setSessionFactory(new org.apache.sshd.server.session.SessionFactory() {
            @Override protected org.apache.sshd.common.session.AbstractSession doCreateSession(org.apache.sshd.common.io.IoSession io) throws Exception {
                return new org.apache.sshd.server.session.ServerSession(server, io) {
                    @Override public void exceptionCaught(Throwable error) { error.printStackTrace(); super.exceptionCaught(error); }
                };
            }
        });
        server.setPublickeyAuthenticator((name, key, session) -> "user".equals(name) && TransferConfig.matchesEncoded(approved, key));
        server.setKeyPairProvider(new AbstractKeyPairProvider() {
            @Override public Iterable<KeyPair> loadKeys() { return Collections.singleton(pair); }
            @Override public KeyPair loadKey(String type) { return "ssh-ed25519".equals(type) ? pair : null; }
            @Override public String getKeyTypes() { return "ssh-ed25519"; }
        });
        java.util.List<org.apache.sshd.common.NamedFactory<org.apache.sshd.common.Signature>> signatures = new ArrayList<>(server.getSignatureFactories());
        signatures.add(new SignatureEd25519.Factory()); server.setSignatureFactories(signatures);
        server.setSubsystemFactories(Collections.singletonList(new org.apache.sshd.common.NamedFactory<org.apache.sshd.server.Command>() {
            @Override public String getName() { return "sftp"; }
            @Override public org.apache.sshd.server.Command create() {
                return new SftpSubsystem() {
                    @Override protected void process(org.apache.sshd.common.util.Buffer buffer) throws IOException {
                        try { super.process(buffer); }
                        catch (Throwable ex) { ex.printStackTrace(); throw new IOException(ex); }
                    }
                };
            }
        }));
        server.setFileSystemFactory(session -> new NativeFileSystemView(session.getUsername(), Collections.singletonMap("/", root.toString()), "/", '/', false));
        server.start();
        String fingerprint = TransferConfig.fingerprint(algo.encodeAsSsh(pair.getPublic()));
        String pcFingerprint = TransferConfig.fingerprint(Base64.getDecoder().decode(pcText.split(" ")[1]));
        Files.writeString(Paths.get(args[2]), server.getPort() + "\n" + fingerprint + "\n" + TransferConfig.pairingCode(fingerprint, pcFingerprint));
        System.in.read();
        server.stop(true);
    }
}
