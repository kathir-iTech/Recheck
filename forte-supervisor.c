#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

static int run_attempt(const char *workflow, int attempt) {
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork FORTE");
        return 0;
    }
    if (pid == 0) {
        execl("/proc/boot/forte", "forte", "-l", "/proc/boot/recheck-station.so",
              "-f", workflow, (char *)NULL);
        perror("exec FORTE");
        _exit(1);
    }
    int status;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status) || WEXITSTATUS(status) != 86) {
        fprintf(stderr, "FORTE_RUN_INCOMPLETE attempt=%d pid=%d\n", attempt, pid);
        fflush(stderr);
        return 0;
    }
    printf("FORTE_EXIT attempt=%d pid=%d status=86\n", attempt, pid);
    fflush(stdout);
    return 1;
}

int main(int argc, char **argv) {
    if (argc != 2 || chdir("/proc/boot") != 0) return EXIT_FAILURE;
    for (int attempt = 1; attempt <= 2; ++attempt) {
        if (!run_attempt(argv[1], attempt)) return EXIT_FAILURE;
    }
    puts("FORTE_RECOVERY_DONE");
    return EXIT_SUCCESS;
}
