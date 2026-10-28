// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	int fd, saved_errno;
	long result;

	if (argc != 2) {
		fprintf(stderr, "Usage: %s module.ko\n", argv[0]);
		return 2;
	}
	fd = open(argv[1], O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		perror("open module");
		return 1;
	}
	result = syscall(SYS_finit_module, fd, "", 0);
	saved_errno = errno;
	close(fd);
	if (result < 0) {
		errno = saved_errno;
		perror("finit_module (no retry)");
		return 1;
	}
	return 0;
}
