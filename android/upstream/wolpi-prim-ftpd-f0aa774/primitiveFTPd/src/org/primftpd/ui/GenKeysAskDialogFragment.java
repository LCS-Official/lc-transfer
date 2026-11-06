package org.primftpd.ui;

import android.app.AlertDialog;
import android.app.Dialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;

import org.primftpd.R;
import org.primftpd.crypto.HostKeyAlgorithm;
import org.primftpd.prefs.LoadPrefsUtil;
import org.primftpd.util.KeyFingerprintProvider;
import org.primftpd.util.ServicesStartStopUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.FileOutputStream;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.DialogFragment;
import androidx.lifecycle.ViewModelProvider;

public class GenKeysAskDialogFragment extends DialogFragment {
    public static final String KEY_START_SERVER = "START_SERVER";

    private final Logger logger = LoggerFactory.getLogger(getClass());

    private boolean startServerOnFinish;

    private SharedViewModel vm;

    @Override
    public void onCreate(@Nullable final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        vm = new ViewModelProvider(requireActivity()).get(SharedViewModel.class);
    }

    @Override
    public void setArguments(Bundle args) {
        super.setArguments(args);
        startServerOnFinish = args.getBoolean(KEY_START_SERVER);
    }

    @Override
    @NonNull
    public Dialog onCreateDialog(Bundle savedInstanceState) {
        logger.debug("showing gen key dialog");
        AlertDialog.Builder builder = new AlertDialog.Builder(getActivity());
        builder.setMessage(R.string.generateKeysMessage);
        builder.setPositiveButton(R.string.generate, (dialog, id) ->
            genKeysAndShowProgressDiag(startServerOnFinish)
        );
        builder.setNegativeButton(R.string.cancel, (dialog, id) -> {
            // nothing
        });
        return builder.create();
    }

    public void genKeysAndShowProgressDiag(boolean startServerOnFinish) {
        logger.trace("genKeysAndShowProgressDiag()");

        Context ctxt = getContext();
        if (ctxt == null) {
            logger.warn("context is null");
            return;
        }

        KeyFingerprintProvider keyFingerprintProvider = vm.getKeyFingerprintProvider();

        // run in background to not block UI
        // note: in previous versions a progress dialog was shown here
        // in recent android versions that is hard to implement
        // on most devices it should be fast enough to give good UX without dialog ...
        try (ExecutorService executorService = Executors.newSingleThreadExecutor()) {
            executorService.execute(() -> {

                // remove old keys
                for (HostKeyAlgorithm hka : HostKeyAlgorithm.values()) {
                    logger.trace("removing keys of hostkey algo {}", hka.getAlgorithmName());
                    try {
                        keyFingerprintProvider.deleteKeyFiles(ctxt, hka);
                    } catch (Exception e) {
                        logger.warn("could not delete old key files for {}", hka.getAlgorithmName());
                    }
                }

                // what keys does user want to have?
                SharedPreferences prefs = LoadPrefsUtil.getPrefs(ctxt);
                Set<String> configuredAlgos = prefs.getStringSet(
                        LoadPrefsUtil.PREF_KEY_HOSTKEY_ALGOS,
                        LoadPrefsUtil.HOSTKEY_ALGOS_DEFAULTS);
                logger.trace("got configured algos {}", configuredAlgos);

                // generate new keys
                for (HostKeyAlgorithm hka : HostKeyAlgorithm.values()) {
                    if (configuredAlgos.contains(hka.getPreferenceValue())) {
                        try (
                            FileOutputStream publickeyFos = keyFingerprintProvider.buildPublickeyOutStream(ctxt, hka);
                            FileOutputStream privatekeyFos = keyFingerprintProvider.buildPrivatekeyOutStream(ctxt, hka)
                        ) {
                            hka.generateKey(publickeyFos, privatekeyFos);
                        } catch (Exception e) {
                            logger.error("could not generate key {}", hka.getAlgorithmName(), e);
                        }
                    }
                }
            });
        }

        // clean up
        // update UI in UI thread
        keyFingerprintProvider.calcPubkeyFingerprints(ctxt);
        vm.updateKeyFingerprints();

        if (startServerOnFinish) {
            // icon members should be set at this time
            ServicesStartStopUtil.startServers(getContext(),
                                               vm.getPrefsBean(),
                                               vm.getKeyFingerprintProvider(),
                                               requireActivity().getSupportFragmentManager(),
                                               vm.getChosenIp());
        }
    }
}
