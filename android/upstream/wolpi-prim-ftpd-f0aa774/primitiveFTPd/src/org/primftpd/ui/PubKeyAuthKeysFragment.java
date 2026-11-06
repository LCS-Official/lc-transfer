package org.primftpd.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import com.google.android.material.floatingactionbutton.FloatingActionButton;

import org.jetbrains.annotations.NotNull;
import org.primftpd.R;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;

public class PubKeyAuthKeysFragment extends Fragment {

    protected final Logger logger = LoggerFactory.getLogger(getClass());

    private SharedViewModel vm;

    @Override
    public void onCreate(@Nullable final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        vm = new ViewModelProvider(requireActivity()).get(SharedViewModel.class);
    }

    @Override
    public View onCreateView(@NotNull LayoutInflater inflater, ViewGroup container,
                             Bundle savedInstanceState) {
        super.onCreateView(inflater, container, savedInstanceState);

        View view = inflater.inflate(R.layout.pubkey_auth_keys, container, false);

        FloatingActionButton addButton = view.findViewById(R.id.addPubkeyAuthKey);
        if (vm.isLeanBack()) {
            addButton.setVisibility(View.GONE);
        } else {
            addButton.setOnClickListener(v -> {
                AddPubkeyAuthKeyDialogFragment addDiag = new AddPubkeyAuthKeyDialogFragment();
                addDiag.show(requireActivity().getSupportFragmentManager(), PftpdFragment.DIALOG_TAG);
            });
        }

        return view;
    }

    @Override
    public void onViewCreated(@NonNull final View view,
                              @Nullable final Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        vm.onDisplayKeys().observe(getViewLifecycleOwner(), this::displayKeys);

        List<String> keys = vm.loadKeysForDisplay(getContext(), logger);
        displayKeys(keys);
    }

    private void displayKeys(List<String> keys) {
        final View view = getView();
        if (view == null) {
            return;
        }
        LinearLayout container = view.findViewById(R.id.pubkeyAuthKeysContainer);
        container.removeAllViews();
        for (String key : keys) {
            TextView textView = new TextView(container.getContext());
            textView.setText(key);
            textView.setPadding(1, 1, 1, 5);
            container.addView(textView);
        }

        if (keys.isEmpty()) {
            TextView textView = new TextView(container.getContext());
            textView.setText(R.string.noKeysPresent);
            container.addView(textView);
        }
    }
}
