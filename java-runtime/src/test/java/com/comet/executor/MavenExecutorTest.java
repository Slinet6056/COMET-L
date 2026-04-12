package com.comet.executor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class MavenExecutorTest {

  @TempDir Path tempDir;

  @Test
  void sanitizeMavenHomeCandidateRejectsWrapperStylePath() throws IOException {
    MavenExecutor executor = new MavenExecutor();
    Path fakeUsr = tempDir.resolve("usr");
    Path binDir = fakeUsr.resolve("bin");
    Files.createDirectories(binDir);
    Files.writeString(binDir.resolve("mvn"), "#!/bin/sh\n");

    assertNull(executor.sanitizeMavenHomeCandidate(fakeUsr.toString()));
  }

  @Test
  void sanitizeMavenHomeCandidateAcceptsRealMavenHome() throws IOException {
    MavenExecutor executor = new MavenExecutor();
    Path mavenHome = tempDir.resolve("apache-maven");
    Path binDir = mavenHome.resolve("bin");
    Files.createDirectories(binDir);
    Files.createDirectories(mavenHome.resolve("conf"));
    Files.createDirectories(mavenHome.resolve("lib"));
    Files.writeString(binDir.resolve("mvn"), "#!/bin/sh\n");

    String resolved = executor.sanitizeMavenHomeCandidate(mavenHome.toString());

    assertNotNull(resolved);
    assertEquals(mavenHome.toRealPath().toString(), resolved);
  }
}
