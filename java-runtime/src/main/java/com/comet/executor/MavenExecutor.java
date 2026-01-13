package com.comet.executor;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.util.Arrays;
import java.util.Properties;

import org.apache.maven.shared.invoker.DefaultInvocationRequest;
import org.apache.maven.shared.invoker.DefaultInvoker;
import org.apache.maven.shared.invoker.InvocationOutputHandler;
import org.apache.maven.shared.invoker.InvocationRequest;
import org.apache.maven.shared.invoker.InvocationResult;
import org.apache.maven.shared.invoker.Invoker;

import com.google.gson.JsonObject;

/**
 * Maven 执行器 - 编译和运行测试
 */
public class MavenExecutor {

    private final Invoker invoker;

    public MavenExecutor() {
        this.invoker = new DefaultInvoker();

        // 尝试找到 Maven 安装路径
        String mavenHome = System.getenv("M2_HOME");
        if (mavenHome == null) {
            mavenHome = System.getenv("MAVEN_HOME");
        }

        // 如果环境变量未设置，尝试通过 which 命令查找 mvn
        if (mavenHome == null) {
            mavenHome = findMavenHomeByWhich();
        }

        if (mavenHome != null) {
            invoker.setMavenHome(new File(mavenHome));
        }
    }

    /**
     * 通过 which 命令查找 Maven 安装路径
     */
    private String findMavenHomeByWhich() {
        try {
            // 尝试多种方式执行 which 命令
            String[] commands = {
                    "which mvn",
                    "/usr/bin/which mvn",
                    "command -v mvn"
            };

            for (String cmd : commands) {
                Process process = Runtime.getRuntime().exec(new String[] { "sh", "-c", cmd });
                BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
                String mvnPath = reader.readLine();
                reader.close();
                process.waitFor();

                if (mvnPath != null && !mvnPath.isEmpty() && new File(mvnPath).exists()) {
                    // 解析真实路径（处理符号链接）
                    File mvnFile = new File(mvnPath).getCanonicalFile();

                    // mvn 通常在 $MAVEN_HOME/bin/mvn
                    File binDir = mvnFile.getParentFile();
                    if (binDir != null && binDir.getName().equals("bin")) {
                        String mavenHome = binDir.getParent();
                        if (mavenHome != null) {
                            return mavenHome;
                        }
                    }

                    // 如果上面没找到，尝试通过 mvn --version 获取 Maven home
                    String mavenHome = findMavenHomeByVersion();
                    if (mavenHome != null) {
                        return mavenHome;
                    }
                }
            }
        } catch (Exception e) {
            // 忽略错误，返回 null
            System.err.println("Warning: Failed to find Maven via 'which' command: " + e.getMessage());
        }
        return null;
    }

    /**
     * 通过 mvn --version 命令获取 Maven home
     */
    private String findMavenHomeByVersion() {
        try {
            Process process = Runtime.getRuntime().exec(new String[] { "sh", "-c", "mvn --version" });
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                // 查找 "Maven home: /path/to/maven"
                if (line.startsWith("Maven home:")) {
                    String mavenHome = line.substring("Maven home:".length()).trim();
                    if (new File(mavenHome).exists()) {
                        return mavenHome;
                    }
                }
            }
            reader.close();
            process.waitFor();
        } catch (Exception e) {
            System.err.println("Warning: Failed to get Maven home from mvn --version: " + e.getMessage());
        }
        return null;
    }

    /**
     * 编译项目
     */
    public JsonObject compile(String projectPath) {
        return executeMaven(projectPath, Arrays.asList("clean", "compile"));
    }

    /**
     * 编译测试代码
     */
    public JsonObject compileTests(String projectPath) {
        // 先 clean 再编译测试，确保测试代码是最新的
        return executeMaven(projectPath, Arrays.asList("clean", "test-compile"));
    }

    /**
     * 运行测试
     */
    public JsonObject runTests(String projectPath) {
        // 在运行测试前先 clean compile，确保变异后的代码被重新编译
        return executeMaven(projectPath, Arrays.asList("clean", "compile", "test"));
    }

    /**
     * 运行测试并生成覆盖率报告
     */
    public JsonObject runTestsWithCoverage(String projectPath) {
        // 使用 JaCoCo Maven 插件
        return executeMaven(projectPath, Arrays.asList("clean", "test", "jacoco:report"));
    }

    /**
     * 执行 Maven 命令
     */
    private JsonObject executeMaven(String projectPath, java.util.List<String> goals) {
        JsonObject result = new JsonObject();

        try {
            InvocationRequest request = new DefaultInvocationRequest();
            request.setPomFile(new File(projectPath, "pom.xml"));
            request.setGoals(goals);
            request.setBatchMode(true);

            // 设置输出处理器
            ByteArrayOutputStream outputStream = new ByteArrayOutputStream();
            PrintStream printStream = new PrintStream(outputStream);

            InvocationOutputHandler outputHandler = printStream::println;
            request.setOutputHandler(outputHandler);

            // 执行
            InvocationResult invocationResult = invoker.execute(request);

            result.addProperty("success", invocationResult.getExitCode() == 0);
            result.addProperty("exitCode", invocationResult.getExitCode());
            result.addProperty("output", outputStream.toString());

            if (invocationResult.getExecutionException() != null) {
                result.addProperty("error", invocationResult.getExecutionException().getMessage());
            }

        } catch (Exception e) {
            result.addProperty("success", false);
            result.addProperty("error", e.getMessage());
        }

        return result;
    }

    /**
     * 运行单个测试类
     */
    public JsonObject runSingleTest(String projectPath, String testClassName) {
        InvocationRequest request = new DefaultInvocationRequest();
        request.setPomFile(new File(projectPath, "pom.xml"));
        request.setGoals(Arrays.asList("test"));

        Properties properties = new Properties();
        properties.setProperty("test", testClassName);
        request.setProperties(properties);

        JsonObject result = new JsonObject();
        try {
            InvocationResult invocationResult = invoker.execute(request);
            result.addProperty("success", invocationResult.getExitCode() == 0);
            result.addProperty("exitCode", invocationResult.getExitCode());
        } catch (Exception e) {
            result.addProperty("success", false);
            result.addProperty("error", e.getMessage());
        }

        return result;
    }

    /**
     * 命令行接口
     */
    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("Usage: MavenExecutor <command> <project_path> [options]");
            System.err.println("Commands: compile, compileTests, test, testWithCoverage, singleTest");
            System.exit(1);
        }

        String command = args[0];
        String projectPath = args[1];

        MavenExecutor executor = new MavenExecutor();
        JsonObject result;

        try {
            switch (command) {
                case "compile":
                    result = executor.compile(projectPath);
                    break;
                case "compileTests":
                    result = executor.compileTests(projectPath);
                    break;
                case "test":
                    result = executor.runTests(projectPath);
                    break;
                case "testWithCoverage":
                    result = executor.runTestsWithCoverage(projectPath);
                    break;
                case "singleTest":
                    if (args.length < 3) {
                        System.err.println("Test class name required for singleTest command");
                        System.exit(1);
                    }
                    result = executor.runSingleTest(projectPath, args[2]);
                    break;
                default:
                    System.err.println("Unknown command: " + command);
                    System.exit(1);
                    return;
            }

            System.out.println(result.toString());
            System.exit(result.get("success").getAsBoolean() ? 0 : 1);

        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }
}
