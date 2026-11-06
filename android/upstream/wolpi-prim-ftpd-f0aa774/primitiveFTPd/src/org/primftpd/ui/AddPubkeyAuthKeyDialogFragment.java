package org.primftpd.ui;

import android.app.AlertDialog;
import android.app.Dialog;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.widget.TextView;
import android.widget.Toast;

import org.primftpd.R;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.DialogFragment;
import androidx.lifecycle.ViewModelProvider;

public class AddPubkeyAuthKeyDialogFragment extends DialogFragment {

    protected final Logger logger = LoggerFactory.getLogger(getClass());

    private SharedViewModel vm;

    @Override
    public void onCreate(@Nullable final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        vm = new ViewModelProvider(requireActivity()).get(SharedViewModel.class);
    }

    @Override
    @NonNull
    public Dialog onCreateDialog(Bundle savedInstanceState) {
        AlertDialog.Builder builder = new AlertDialog.Builder(getActivity());
        builder.setMessage(R.string.addPubkeyForAuth);

        LayoutInflater inflater = requireActivity().getLayoutInflater();
        View view = inflater.inflate(R.layout.dialog_add_key, null);
        builder.setView(view);

        builder.setPositiveButton(R.string.add, (dialog, id) -> {
            TextView textbox = view.findViewById(R.id.addPubkeyTextbox);
            CharSequence key = textbox.getText();
            String msg = vm.addKeyToFile(view.getContext(), key, logger);
            if (msg != null) {
                Toast.makeText(view.getContext(), msg, Toast.LENGTH_SHORT).show();
            }
        });
        builder.setNegativeButton(R.string.cancel, (dialog, id) -> {
            // nothing
        });

        return builder.create();
    }
}
