# Various TODOs (or not-TODOs) for the future


## Flatpak Support in Sandkasten

### Current Status

Partial flatpak support has been implemented (directories are bound, XDG_DATA_DIRS is set), but flatpak applications fail to run with the following errors:

```
(flatpak run:54): GLib-WARNING **: 12:52:19.631: getpwuid_r(): failed due to unknown user id (1001)
F: Can't get document portal: GDBus.Error:org.freedesktop.DBus.Error.NoReply: Message recipient disconnected from message bus without replying
bwrap: Can't find source path /run/user/1001/.flatpak-helper/monitor: No such file or directory
```

### Problems Identified

#### 1. User Namespace Isolation Issue
- **Problem**: The `--unshare-user-try` flag creates a user namespace where UID 1001 doesn't have proper mapping
- **Symptom**: `getpwuid_r(): failed due to unknown user id (1001)`
- **Impact**: Flatpak cannot resolve the user ID, causing initialization failures

#### 2. Isolated D-Bus Session
- **Problem**: Sandkasten creates an isolated D-Bus daemon (lines 195-217 in `sandkasten` script)
- **Symptom**: `Can't get document portal: GDBus.Error:org.freedesktop.DBus.Error.NoReply`
- **Impact**: Flatpak cannot communicate with xdg-desktop-portal, xdg-document-portal, and other portal services that run on the host D-Bus
- **Current Implementation**: 
  ```bash
  dbus-daemon --session --address="unix:path=$SANDBOX_DBUS_DIR/bus" --nofork --print-pid
  ## Then binds: --bind "$SANDBOX_DBUS_DIR/bus" "$XDG_RUNTIME_DIR/bus"
  ```

#### 3. Missing XDG_RUNTIME_DIR Contents
- **Problem**: Flatpak needs access to `$XDG_RUNTIME_DIR/.flatpak/` directory
- **Host Location**: `/run/user/1001/.flatpak/` (contains subdirectories like `200786582/`, `org.gnome.meld/`, etc.)
- **Symptom**: `Can't find source path /run/user/1001/.flatpak-helper/monitor`
- **Impact**: Flatpak helper processes and monitors cannot function

#### 4. No Portal Access
- **Problem**: Flatpak applications rely on various portals (document, desktop, etc.) that run as systemd user services on the host
- **Missing Services**: 
  - `xdg-document-portal`
  - `xdg-desktop-portal`
  - `xdg-desktop-portal-gtk` (or other backend)
- **Impact**: File dialogs, desktop integration, and other features don't work

### Proposed Solutions

#### Approach 1: Share Host D-Bus (Recommended)

This is the simpler approach and matches what most sandboxing solutions do (Flatpak itself, Firejail, etc.).

**Changes needed in `sandkasten` script:**

1. **Remove isolated D-Bus creation** (lines 191-217):
   - Delete the D-Bus daemon startup code
   - Remove the cleanup trap

2. **Bind host D-Bus socket**:
   ```bash
   ## Replace the isolated D-Bus bind with:
   --ro-bind "$XDG_RUNTIME_DIR/bus" "$XDG_RUNTIME_DIR/bus" \
   ```

3. **Bind flatpak runtime directory**:
   ```bash
   ## Add after the Wayland bind:
   --bind "$XDG_RUNTIME_DIR/.flatpak" "$XDG_RUNTIME_DIR/.flatpak" \
   ```

4. **Keep host D-Bus address**:
   ```bash
   --setenv DBUS_SESSION_BUS_ADDRESS "$DBUS_SESSION_BUS_ADDRESS" \
   ```

5. **Re-enable flatpak directory binds** (removed for now but documented here):
   ```bash
   ## Create Flatpak user directory snapshot/copy
   FLATPAK_USER_SANDBOX="$SANDBOX_HOME/.local/share/flatpak"
   if [ -d "$HOME/.local/share/flatpak" ]; then
       echo "Creating Flatpak user directory snapshot..."
       mkdir -p "$SANDBOX_HOME/.local/share"
       if btrfs subvolume snapshot "$HOME/.local/share/flatpak" "$FLATPAK_USER_SANDBOX" 2>/dev/null; then
           echo "BTRFS snapshot created successfully"
       else
           echo "BTRFS snapshot failed, copying with COW if available..."
           cp -r "$HOME/.local/share/flatpak" "$FLATPAK_USER_SANDBOX"
       fi
   else
       mkdir -p "$FLATPAK_USER_SANDBOX"
   fi
   COW_BINDS="$COW_BINDS --bind $FLATPAK_USER_SANDBOX /home/$USERNAME/.local/share/flatpak"
   ```

6. **Add to cleanup** (both scripts):
   ```bash
   ## Add to subvolume list:
   for subvol in "$JULIA_DEPOT_SANDBOX" "$JULIAUP_SANDBOX" "$TECTONIC_CACHE_SANDBOX" "$FLATPAK_USER_SANDBOX"; do
   ```

**Pros:**
- Simple to implement
- Full flatpak functionality (portals work)
- Matches industry standard approach
- Applications can use system services

**Cons:**
- Slightly reduced isolation (sandbox can communicate with host D-Bus services)
- Sandboxed processes could potentially interact with host services

#### Approach 2: Isolated D-Bus with Portal Bridge (Complex)

Keep the isolated D-Bus but set up portal services inside the sandbox.

**Changes needed:**

1. **Start portal services in sandbox**:
   - Would need to run `xdg-desktop-portal` and backends inside sandbox
   - Requires proper systemd user session or manual service management
   - Much more complex

2. **Handle user namespace mapping**:
   ```bash
   ## Add UID/GID mappings:
   --uid-map 0 1001 1 \
   --gid-map 0 1001 1 \
   ```

3. **Create flatpak helper infrastructure**:
   - Set up monitor sockets in `$XDG_RUNTIME_DIR/.flatpak-helper/`
   - May require running `flatpak-session-helper`

**Pros:**
- Stronger isolation
- No access to host D-Bus services

**Cons:**
- Much more complex to implement and maintain
- Portal services might not work properly in isolation
- May have limited functionality
- Requires deep understanding of portal architecture

#### Approach 3: Remove User Namespace Isolation (Partial Fix)

Remove or modify the `--unshare-user-try` flag and share host D-Bus.

**Changes needed:**

1. **Modify unshare flags**:
   ```bash
   ## Change from:
   bwrap --unshare-user-try --unshare-pid --unshare-uts --unshare-cgroup \
   
   ## To:
   bwrap --unshare-pid --unshare-uts --unshare-cgroup \
   ```

2. **Apply Approach 1 changes** (share host D-Bus)

**Pros:**
- Fixes the UID mapping issue
- Simple to implement
- Full flatpak functionality

**Cons:**
- Reduces isolation (no user namespace)
- UID is the same as host (less protection)

### Recommended Approach

**Approach 1 (Share Host D-Bus)** is recommended because:
- It's the simplest to implement
- It provides full flatpak functionality
- It matches how other sandboxing solutions work
- The isolation level is still reasonable for a development sandbox

### Implementation Checklist

When implementing Approach 1:

- [ ] Remove isolated D-Bus daemon startup (lines 191-217)
- [ ] Remove D-Bus cleanup trap
- [ ] Change D-Bus socket bind to use host socket (read-only)
- [ ] Add bind for `$XDG_RUNTIME_DIR/.flatpak`
- [ ] Keep host `DBUS_SESSION_BUS_ADDRESS` environment variable
- [ ] Re-enable flatpak user directory snapshot/bind (see code above)
- [ ] Update cleanup to include `$FLATPAK_USER_SANDBOX` in subvolume list
- [ ] Test with: `flatpak run org.gnome.meld`
- [ ] Verify portals work (file dialogs, etc.)
- [ ] Update documentation

### Testing Commands

After implementation, test with:

```bash
## List flatpak apps
flatpak list

## Run an app
flatpak run org.gnome.meld

## Check D-Bus connection
busctl --user list | grep portal

## Test file dialog in flatpak app
flatpak run org.gnome.meld /path/to/file1 /path/to/file2
```

### Additional Notes

- The system flatpak directory `/var/lib/flatpak` is already bound as read-only (line 254)
- The `flatpak` binary is available from `/usr/bin` (already bound)
- Flatpak's own sandboxing (via bwrap) provides additional security even when run from within another sandbox
- The `XDG_DATA_DIRS` environment variable should be set to include flatpak exports (already implemented)

### References

- Flatpak architecture: https://docs.flatpak.org/en/latest/under-the-hood.html
- XDG Desktop Portals: https://flatpak.github.io/xdg-desktop-portal/
- Bubblewrap (bwrap) documentation: https://github.com/containers/bubblewrap

### Related Files

- `/var/home/m3/my_programs/sandkasten/sandkasten` - Main script
- `/var/home/m3/my_programs/sandkasten/sandkasten-cleanup` - Cleanup script
