package org.primftpd.filesystem;

import org.apache.sshd.common.Session;
import org.apache.sshd.common.file.FileSystemView;
import org.apache.sshd.common.file.SshFile;
import org.primftpd.services.PftpdService;

import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Map;
import org.primftpd.transfer.MediaCatalog;

public class FsSshFileSystemView extends FsFileSystemView<FsSshFile, SshFile> implements FileSystemView {

	private final File homeDir;
	private final Session session;

	public FsSshFileSystemView(PftpdService pftpdService, File homeDir, Session session) {
		super(pftpdService);
		this.homeDir = homeDir;
		this.session = session;
	}

	@Override
	protected FsSshFile createFile(File file) {
		String prefix = homeDir.getAbsolutePath() + "/.lc-transfer/";
		String path = file.getAbsolutePath();
		if (path.equals(prefix + "catalog.json.gz") || (path.startsWith(prefix + "previews/") && path.endsWith(".jpg"))) {
			File generated;
			try {
				generated = path.equals(prefix + "catalog.json.gz")
					? MediaCatalog.catalog(pftpdService.getContext(), homeDir)
					: MediaCatalog.preview(pftpdService.getContext(), homeDir, path.substring((prefix + "previews/").length(), path.length() - 4));
			} catch (IOException ex) {
				logger.debug("Generated media unavailable: {}", ex.getMessage());
				generated = new File(pftpdService.getContext().getCacheDir(), "unavailable-media");
			}
			return new GeneratedFile(this, generated, path, file.getName());
		}
		return new FsSshFile(this, file, session);
	}

	private class GeneratedFile extends FsSshFile {
		private final String virtualPath, virtualName;
		GeneratedFile(FsSshFileSystemView view, File file, String path, String name) {
			super(view, file, FsSshFileSystemView.this.session); virtualPath = path; virtualName = name;
		}
		@Override public String getAbsolutePath() { return virtualPath; }
		@Override public String getName() { return virtualName; }
		@Override public boolean isWritable() { return false; }
		@Override public boolean mkdir() { return false; }
		@Override public boolean delete() { return false; }
		@Override public boolean move(SshFile destination) { return false; }
		@Override public boolean move(AbstractFile<FsSshFileSystemView> destination) { return false; }
		@Override public boolean create() throws IOException { throw new IOException("Read-only generated media"); }
		@Override public void truncate() throws IOException { throw new IOException("Read-only generated media"); }
		@Override public OutputStream createOutputStream(long offset) throws IOException { throw new IOException("Read-only generated media"); }
		@Override public void setAttributes(Map<SshFile.Attribute, Object> attributes) throws IOException { throw new IOException("Read-only generated media"); }
	}

	@Override
	protected String absolute(String file) {
		try {
			File root = homeDir.getCanonicalFile();
			File target = new File("/".equals(file) ? root.getPath() : Utils.absoluteOrHome(file, root.getPath())).getCanonicalFile();
			if (!target.equals(root) && !target.getPath().startsWith(root.getPath() + "/"))
				throw new SecurityException("Outside shared storage");
			return target.getPath();
		} catch (java.io.IOException ex) { throw new SecurityException("Invalid file path", ex); }
	}

	@Override
	public SshFile getFile(SshFile baseDir, String file) {
		logger.trace("getFile(baseDir: {}, file: {})", baseDir.getAbsolutePath(), file);
		// e.g. for scp
		return getFile(baseDir.getAbsolutePath() + "/" + file);
	}

	@Override
	public FileSystemView getNormalizedView() {
		logger.trace("getNormalizedView()");
		return this;
	}
}
