package org.primftpd.ui;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.security.PublicKey;
import java.util.ArrayList;
import java.util.List;

import org.primftpd.R;
import org.primftpd.pojo.KeyParser;
import org.primftpd.prefs.LoadPrefsUtil;
import org.primftpd.prefs.PrefsBean;
import org.primftpd.util.Defaults;
import org.primftpd.util.KeyFingerprintProvider;
import org.primftpd.util.ServersRunningBean;
import org.primftpd.util.ServicesStartStopUtil;
import org.slf4j.Logger;

/**
 * This is not the best code I wrote.
 * But it's a meant as a quick fix to GitHub #437 by simply moving existing code
 * to this ViewModel.
 */
public class SharedViewModel
        extends ViewModel {

    private final KeyFingerprintProvider keyFingerprintProvider = new KeyFingerprintProvider();
    private boolean leanBack;
    private String chosenIp;
    private PrefsBean prefsBean;

    private int fingerprintsFragmentTabIndex;

    private final MutableLiveData<Void> showKeyFingerprints = new MutableLiveData<>();
    private final MutableLiveData<Void> updateQrCode = new MutableLiveData<>();

    private final MutableLiveData<List<String>> displayKeys = new MutableLiveData<>();

    MutableLiveData<Void> onShowKeyFingerprints() {
        return showKeyFingerprints;
    }

    MutableLiveData<Void> onUpdateQrCode() {
        return updateQrCode;
    }

    MutableLiveData<List<String>> onDisplayKeys() {
        return displayKeys;
    }

    void updateKeyFingerprints() {
        showKeyFingerprints.setValue(null);
    }

    void init(Context context,
              Logger logger) {
        loadPrefs(context, logger);
    }

    void loadPrefs(Context context,
                   Logger logger) {
        logger.debug("loadPrefs()");

        SharedPreferences prefs = LoadPrefsUtil.getPrefs(context);
        this.prefsBean = LoadPrefsUtil.loadPrefs(logger, prefs);
    }

    /**
     * Load the previously loaded bean.
     *
     * @return bean
     */
    public PrefsBean getPrefsBean() {
        return prefsBean;
    }

    boolean isLeanBack() {
        return leanBack;
    }

    void setLeanBack(final boolean leanBack) {
        this.leanBack = leanBack;
    }

    String getChosenIp() {
        return chosenIp;
    }

    void setChosenIp(final String chosenIp) {
        this.chosenIp = chosenIp;
    }

    public int getFingerprintsFragmentTabIndex() {
        return fingerprintsFragmentTabIndex;
    }

    public void setFingerprintsFragmentTabIndex(final int fingerprintsFragmentTabIndex) {
        this.fingerprintsFragmentTabIndex = fingerprintsFragmentTabIndex;
    }

    KeyFingerprintProvider getKeyFingerprintProvider() {
        return keyFingerprintProvider;
    }

    void updateQrCode() {
        updateQrCode.setValue(null);
    }

    List<String> loadKeysForDisplay(Context context,
                                    Logger logger) {
        String[] keyPaths = {
                Defaults.PUB_KEY_AUTH_KEY_PATH_OLDER,
                Defaults.PUB_KEY_AUTH_KEY_PATH_OLD,
                Defaults.pubKeyAuthKeyPath(context),
        };

        List<String> keys = new ArrayList<>();
        for (String path : keyPaths) {
            keys.addAll(loadKeysOfFile(path, logger));
        }
        return keys;
    }

    private List<String> loadKeysOfFile(String path, Logger logger) {
        List<String> keys = new ArrayList<>();
        try {
            try (FileReader filereader = new FileReader(path)) {
                BufferedReader reader = new BufferedReader(filereader);
                while (reader.ready()) {
                    String line = reader.readLine();
                    if (!line.startsWith("#") && !line.isEmpty()) {
                        keys.add(line);
                    }
                }
            }
        } catch (IOException e) {
            logger.info("could not load key of path '{}': {}, {}",
                        path, e.getClass().getName(), e.getMessage());
        }
        return keys;
    }

    private boolean validateKey(@NonNull CharSequence key) {
        PublicKey pubKey = null;
        try {
            pubKey = KeyParser.parseKeyLine(key.toString());
        } catch (Exception e) {
            // handled by having pubKey equal null
        }
        return pubKey != null;
    }

    /**
     * Add the given key to the {@code authorized_keys}.
     *
     * @param context context to be used
     * @param key key to be stored
     * @param logger logger to be used
     *
     * @return a message to show to the user, of {@code null} for none.
     */
    @Nullable
    public String addKeyToFile(@NonNull Context context,
                               @NonNull CharSequence key,
                               @NonNull Logger logger) {
        final String path = Defaults.pubKeyAuthKeyPath(context);
        if (validateKey(key)) {
            try {
                try (FileWriter writer = new FileWriter(path, true)) {
                    writer.append("\n");
                    writer.append(key);
                }

                List<String> keys = loadKeysForDisplay(context, logger);
                displayKeys.setValue(keys);

                ServersRunningBean serversRunningBean =
                        ServicesStartStopUtil.checkServicesRunning(context);
                if (serversRunningBean.atLeastOneRunning()) {
                    return context.getString(R.string.restartServer);
                }
            } catch (IOException e) {
                logger.info("could not store key in file '{}': {}, {}",
                            path, e.getClass().getName(), e.getMessage());
            }
        } else {
            return context.getString(R.string.pubkeyInvalid);
        }

        return null;
    }
}
