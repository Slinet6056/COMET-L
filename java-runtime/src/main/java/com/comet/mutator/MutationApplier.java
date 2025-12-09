package com.comet.mutator;

import com.comet.models.MutationPatch;
import com.google.gson.Gson;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

/**
 * 变异应用器 - 将变异补丁应用到源代码
 */
public class MutationApplier {

    private final Gson gson;

    public MutationApplier() {
        this.gson = new Gson();
    }

    /**
     * 应用变异补丁
     */
    public String applyMutation(String sourceFilePath, String patchJson, String outputPath) throws IOException {
        MutationPatch patch = gson.fromJson(patchJson, MutationPatch.class);

        // 读取源文件
        Path sourcePath = Paths.get(sourceFilePath);
        List<String> lines = Files.readAllLines(sourcePath);

        // 应用变异
        List<String> mutatedLines = new ArrayList<>();
        for (int i = 0; i < lines.size(); i++) {
            int lineNum = i + 1;
            if (lineNum < patch.getLineStart() || lineNum > patch.getLineEnd()) {
                // 保持原样
                mutatedLines.add(lines.get(i));
            } else if (lineNum == patch.getLineStart()) {
                // 插入变异代码（按行分割）
                String[] mutatedCodeLines = patch.getMutatedCode().split("\n");
                for (String mutatedLine : mutatedCodeLines) {
                    mutatedLines.add(mutatedLine);
                }
                // 跳过其余被替换的行
            }
        }

        // 写入输出文件
        Path outputFilePath = Paths.get(outputPath);
        Files.createDirectories(outputFilePath.getParent());
        Files.write(outputFilePath, mutatedLines);

        return outputPath;
    }

    /**
     * 批量应用变异（创建多个变异体文件）
     */
    public List<String> applyMutations(String sourceFilePath, String patchesJson, String outputDir) throws IOException {
        MutationPatch[] patches = gson.fromJson(patchesJson, MutationPatch[].class);
        List<String> outputPaths = new ArrayList<>();

        for (int i = 0; i < patches.length; i++) {
            String outputPath = outputDir + "/mutant_" + i + ".java";
            String patchJson = gson.toJson(patches[i]);
            applyMutation(sourceFilePath, patchJson, outputPath);
            outputPaths.add(outputPath);
        }

        return outputPaths;
    }

    /**
     * 验证变异代码是否可以编译
     */
    public boolean validateCompilation(String mutatedFilePath, String classpath) {
        try {
            ProcessBuilder pb = new ProcessBuilder(
                    "javac",
                    "-cp", classpath,
                    mutatedFilePath);
            pb.redirectErrorStream(true);
            Process process = pb.start();

            int exitCode = process.waitFor();
            return exitCode == 0;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * 命令行接口
     */
    public static void main(String[] args) {
        if (args.length < 3) {
            System.err.println("Usage: MutationApplier <source_file> <patch_json> <output_path> [validate]");
            System.err.println("Received " + args.length + " arguments:");
            for (int i = 0; i < args.length; i++) {
                System.err.println("  args[" + i + "]: " + args[i].substring(0, Math.min(100, args[i].length())));
            }
            System.exit(1);
        }

        String sourceFile = args[0];
        String patchJson = args[1];
        String outputPath = args[2];
        boolean validate = args.length > 3 && args[3].equals("validate");

        MutationApplier applier = new MutationApplier();

        try {
            String result = applier.applyMutation(sourceFile, patchJson, outputPath);
            System.out.println("Mutation applied: " + result);

            if (validate) {
                boolean valid = applier.validateCompilation(result, ".");
                System.out.println("Compilation valid: " + valid);
                System.exit(valid ? 0 : 1);
            }
        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }
}
