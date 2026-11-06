package org.primftpd.crypto;

import org.apache.sshd.common.NamedFactory;
import org.apache.sshd.common.Signature;
import org.apache.sshd.common.signature.AbstractSignature;
import org.bouncycastle.crypto.Signer;
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters;
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters;
import org.bouncycastle.crypto.signers.Ed25519Signer;
import org.bouncycastle.crypto.util.PrivateKeyFactory;
import org.bouncycastle.crypto.util.PublicKeyFactory;
import org.bouncycastle.jcajce.provider.asymmetric.edec.BCEdDSAPrivateKey;
import org.bouncycastle.jcajce.provider.asymmetric.edec.BCEdDSAPublicKey;
import org.primftpd.pojo.KeyParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.reflect.Field;
import java.security.PrivateKey;
import java.security.PublicKey;

public class SignatureEd25519 extends AbstractSignature {

    public static class Factory implements NamedFactory<Signature> {

        public String getName() {
            return KeyParser.NAME_ED25519;
        }

        public Signature create() {
            return new SignatureEd25519(getName());
        }

    }

    private SignatureEd25519(String name) {
        super(name);
    }

    private final Logger logger = LoggerFactory.getLogger(getClass());

    private Signer signer = new Ed25519Signer();

    public void init(PublicKey pubkey, PrivateKey prvkey) {
        try {
            // Decode standard key encodings instead of reflecting into BC's private fields.
            if (prvkey != null) {
                signer.init(true, PrivateKeyFactory.createKey(prvkey.getEncoded()));
            } else if (pubkey != null) {
                signer.init(false, PublicKeyFactory.createKey(pubkey.getEncoded()));
            } else {
                throw new IllegalArgumentException("Missing Ed25519 key");
            }
        } catch (java.io.IOException ex) {
            throw new IllegalArgumentException("Invalid Ed25519 key", ex);
        }
    }

    public void update(byte[] H, int off, int len) {
        signer.update(H, off, len);
    }

    public boolean verify(byte[] sig) {
        sig = extractSig(sig);
        return signer.verifySignature(sig);
    }

    public byte[] sign() throws Exception {
        return signer.generateSignature();
    }
}
