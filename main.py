import logging
import os
from ruamel.yaml import YAML
import shutil
import subprocess
import sys
import traceback


# TODO support for subdirectory of git repo


svn = ["svn", "--non-interactive"]


def run_command_and_return_output(cmd, cwd=None):
    output = subprocess.check_output(cmd, cwd=cwd)
    output = [s.strip().decode("utf-8", errors="ignore") for s in output.splitlines()]
    return output


class GitSVNSyncTool(object):
    def __init__(self, config_file):
        global svn
        self.logger = logging.getLogger("sync_tool")
        self.logger.setLevel(logging.DEBUG)
        log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', '%H:%M:%S')
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(log_formatter)
        self.logger.addHandler(stream_handler)

        with open(config_file) as conf:
            self.config = YAML(typ="safe").load(conf.read())

        if self.config["svn_username"] is not None and self.config["svn_password"] is not None:
            svn += ["--username", self.config["svn_username"]]
            svn += ["--password", self.config["svn_password"]]
            svn += ["--no-auth-cache"]

        self.git_local_root = os.path.join(os.getcwd(), "git_repo")
        if not os.path.exists(self.git_local_root):
            clone = run_command_and_return_output(["git", "clone", self.config["git_remote"], self.git_local_root])
            self.logger.debug("Cloned remote git repo: {!r}".format("; ".join(clone)))

        self.svn_local_root = os.path.join(os.getcwd(), "svn_repo")
        if not os.path.exists(self.svn_local_root):
            cmd = list(svn)
            cmd += ["checkout", self.config["svn_remote"], self.svn_local_root]
            checkout = run_command_and_return_output(cmd)
            self.logger.debug("Checked out remote SVN repo: {!r}".format("; ".join(checkout)))

    def get_git_changes(self):
        current_commit_hash = subprocess.check_output(["git", "-C", self.git_local_root, "rev-parse", "--short", "HEAD"])
        current_commit_hash = current_commit_hash.strip().decode("utf-8", "ignore")
        subprocess.check_output(["git", "-C", self.git_local_root, "fetch"])

        # Get changes/commits since last update
        commits = run_command_and_return_output(["git", "-C", self.git_local_root, "log",
                                                 "--no-merges", "--pretty=format:%s", "..origin/master"])
        commits = list(reversed(commits))

        self.logger.debug("Git commits since {}: {!r}".format(current_commit_hash, " | ".join(commits)))
        # Get changed files
        changed_files = []
        if not len(commits) == 0:
            changed_files = run_command_and_return_output(["git", "-C", self.git_local_root, "show",
                                                           "--pretty=format:", "--name-only", "..origin/master"])
            self.logger.debug("Git changes since {}: {!r}".format(current_commit_hash, ", ".join(changed_files)))

        return changed_files

    def get_current_svn_revision(self):
        output = run_command_and_return_output(["svn", "info"], cwd=self.svn_local_root)
        for line in output:
            if line.startswith("Revision"):
                return line.split(" ")[1]

    def get_svn_changes(self):
        # Get current revision we are on
        current_svn_revision = self.get_current_svn_revision()

        cmd = list(svn)
        if self.config["svn_username"] is not None and self.config["svn_password"] is not None:
            cmd += ["--username", self.config["svn_username"]]
            cmd += ["--password", self.config["svn_password"]]
            cmd += ["--no-auth-cache"]
        cmd += ["diff", "-r", "{}:HEAD".format(current_svn_revision), "--summarize"]

        output = subprocess.check_output(cmd, cwd=self.svn_local_root)
        changed_files = [s.strip().decode("utf-8", "ignore").split(" ")[-1] for s in output.splitlines()]
        # svn diff --summarize returns a list of file names preceded by the type of change (A/M/U/?) and ~6 spaces
        # splitting on space and getting last should give a list of just file names
        self.logger.debug("SVN changes since revision {}: {!r}".format(current_svn_revision, ", ".join(changed_files)))
        return changed_files

    def update_git_from_remote(self):
        """
        Returns true/false, error info if any, and repo status
        (True, None, <git status output>)
        (False, <commit messages containing fault>, <git status output>)
        """
        return_code = subprocess.check_call(["git", "-C", self.git_local_root, "pull"])
        if return_code != 0:
            error_info = run_command_and_return_output(["git", "-C", self.git_local_root, "log",
                                                        "--no-merges", "--pretty=format:%s", "..origin/master"])
            error_info = list(reversed(error_info))
            self.logger.debug(error_info)
        else:
            error_info = None

        status = run_command_and_return_output(["git", "-C", self.git_local_root, "status"])
        self.logger.debug(status)
        return error_info is None, error_info, status

    def update_svn_from_remote(self):
        """
        Returns true/false, error info if any, and repo status
        (True, None, <svn status output>)
        (False, <svn update return code and output>, <svn status output>)
        """
        try:
            update = list(svn) + ["update"]
            update_return = run_command_and_return_output(update, cwd=self.svn_local_root)
            self.logger.debug(update_return)
        except Exception:
            error_info = traceback.format_exc()
            self.logger.debug(error_info)
        else:
            error_info = None
        status = list(svn) + ["status"]
        status = run_command_and_return_output(status, cwd=self.svn_local_root)
        self.logger.debug(status)
        return error_info is None, error_info, status

    def git_to_svn(self, file_list):
        svn_update_success, svn_error, svn_status = self.update_svn_from_remote()
        git_update_success, git_error, git_status = self.update_git_from_remote()
        # TODO log errors and notify
        add = list(svn) + ["add", "--force"]
        commit = list(svn) + ["commit", "-m", "\"Sync from Git\""]
        update = list(svn) + ["update"]
        if svn_update_success and git_update_success:
            for file in file_list:
                if os.path.exists(os.path.join(self.svn_local_root, file)):
                    os.remove(os.path.join(self.svn_local_root, file))
                shutil.copy2(os.path.join(self.git_local_root, file), self.svn_local_root)
                add_return = run_command_and_return_output(add + [file], cwd=self.svn_local_root)
                self.logger.debug("SVN add: {}".format(add_return))
            commit_return = run_command_and_return_output(commit, cwd=self.svn_local_root)
            self.logger.debug("SVN commit: {}".format(commit_return))
            update_return = run_command_and_return_output(update, cwd=self.svn_local_root)
            self.logger.debug("SVN update: {}".format(update_return))

    def svn_to_git(self, file_list):
        svn_update_success, svn_error, svn_status = self.update_svn_from_remote()
        git_update_success, git_error, git_status = self.update_git_from_remote()
        # TODO log errors and notify
        if svn_update_success and git_update_success:
            for file in file_list:
                if os.path.exists(os.path.join(self.git_local_root, file)):
                    os.remove(os.path.join(self.git_local_root, file))
                shutil.copy2(os.path.join(self.svn_local_root, file), self.git_local_root)
                add_return = run_command_and_return_output(["git", "-C", self.git_local_root, "add", file])
                self.logger.debug("Git add: {}".format(add_return))
            commit_return = run_command_and_return_output(["git", "-C", self.git_local_root,
                                                           "commit", "-m", "Sync from SVN"])
            self.logger.debug("Git commit: {}".format(commit_return))

            push_return = run_command_and_return_output(["git", "-C", self.git_local_root, "push"])
            self.logger.debug("Git push: {}".format(push_return))

    def sync_changes(self):
        git_changes = self.get_git_changes()
        svn_changes = self.get_svn_changes()

        # Use a set to check, it's significantly faster than membership checking against a list
        temp = set(svn_changes)
        conflicts = [change for change in git_changes if change in temp]

        if len(git_changes) == 0 and len(svn_changes) == 0:
            self.logger.info("No changes.")
            return
        elif len(git_changes) == 0 and len(svn_changes) > 0:
            # Sync changes from SVN to Git
            self.logger.info("Syncing to git: {}".format(", ".join(svn_changes)))
            self.svn_to_git(svn_changes)
        elif len(git_changes) > 0 and len(svn_changes) == 0:
            # Sync changes from Git to SVN
            self.logger.info("Syncing to SVN: {}".format(", ".join(git_changes)))
            self.git_to_svn(git_changes)
        else:
            # Changes in both repositories, try to resolve things
            if len(conflicts) == 0:
                # If there are no conflicts, just try to sync.
                # Things should work out, since we don't care much about history.
                self.logger.info("No conflicts.")
                self.logger.info("Syncing to git: {}".format(", ".join(svn_changes)))
                self.svn_to_git(svn_changes)
                self.logger.info("Syncing to SVN: {}".format(", ".join(git_changes)))
                self.git_to_svn(git_changes)
            else:
                # Potential conflicts
                # SVN is master
                # SVN changes that are also in git changes are are overwritten in git (simply sync all SVN changes to git)
                # git changes that aren't in SVN changes are synced to SVN, conflicts are simply not synced (edits are thus "discarded")
                self.logger.warning("Possibly conflicting changes in {}".format(", ".join(conflicts)))
                self.logger.info("Syncing to git: {}".format(", ".join(svn_changes)))
                self.svn_to_git(svn_changes)
                git_okays = [change for change in git_changes if change not in set(svn_changes)]
                self.logger.info("Syncing to SVN: {}".format(", ".join(git_okays)))
                self.git_to_svn(git_okays)


if __name__ == "__main__":
    sync_tool = GitSVNSyncTool("config.yml")
    sync_tool.sync_changes()
