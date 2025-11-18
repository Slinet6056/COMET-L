package com.comet.analyzer;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.comments.JavadocComment;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * 代码分析器 - 使用 JavaParser 提取代码信息
 */
public class CodeAnalyzer {

    private final JavaParser javaParser;
    private final Gson gson;

    public CodeAnalyzer() {
        this.javaParser = new JavaParser();
        this.gson = new Gson();
    }

    /**
     * 分析 Java 源文件
     */
    public String analyzeFile(String filePath) throws Exception {
        File file = new File(filePath);
        if (!file.exists()) {
            throw new IOException("File not found: " + filePath);
        }

        ParseResult<CompilationUnit> parseResult = javaParser.parse(file);

        if (!parseResult.isSuccessful()) {
            throw new Exception("Failed to parse file: " + filePath);
        }

        CompilationUnit cu = parseResult.getResult().orElseThrow(() ->
            new RuntimeException("Parse result is empty"));
        JsonObject result = new JsonObject();

        // 提取包名
        cu.getPackageDeclaration().ifPresent(pd ->
            result.addProperty("package", pd.getNameAsString())
        );

        // 提取类信息
        JsonArray classes = new JsonArray();
        cu.findAll(ClassOrInterfaceDeclaration.class).forEach(cls -> {
            JsonObject classInfo = new JsonObject();
            classInfo.addProperty("name", cls.getNameAsString());
            classInfo.addProperty("isInterface", cls.isInterface());
            classInfo.addProperty("isPublic", cls.isPublic());

            // 提取方法
            JsonArray methods = new JsonArray();
            cls.getMethods().forEach(method -> {
                JsonObject methodInfo = extractMethodInfo(method);
                methods.add(methodInfo);
            });
            classInfo.add("methods", methods);

            classes.add(classInfo);
        });
        result.add("classes", classes);

        return gson.toJson(result);
    }

    /**
     * 提取方法信息
     */
    private JsonObject extractMethodInfo(MethodDeclaration method) {
        JsonObject methodInfo = new JsonObject();
        methodInfo.addProperty("name", method.getNameAsString());
        methodInfo.addProperty("signature", method.getDeclarationAsString(false, false, false));
        methodInfo.addProperty("isPublic", method.isPublic());
        methodInfo.addProperty("isPrivate", method.isPrivate());
        methodInfo.addProperty("isStatic", method.isStatic());

        // 提取 Javadoc
        Optional<JavadocComment> javadoc = method.getJavadocComment();
        if (javadoc.isPresent()) {
            methodInfo.addProperty("javadoc", javadoc.get().getContent());
        }

        // 提取参数
        JsonArray params = new JsonArray();
        method.getParameters().forEach(param -> {
            JsonObject paramInfo = new JsonObject();
            paramInfo.addProperty("name", param.getNameAsString());
            paramInfo.addProperty("type", param.getTypeAsString());
            params.add(paramInfo);
        });
        methodInfo.add("parameters", params);

        // 提取返回类型
        methodInfo.addProperty("returnType", method.getTypeAsString());

        // 提取代码范围
        if (method.getRange().isPresent()) {
            JsonObject range = new JsonObject();
            range.addProperty("begin", method.getRange().get().begin.line);
            range.addProperty("end", method.getRange().get().end.line);
            methodInfo.add("range", range);
        }

        return methodInfo;
    }

    /**
     * 获取类的所有 public 方法
     */
    public String getPublicMethods(String filePath) throws Exception {
        File file = new File(filePath);
        ParseResult<CompilationUnit> parseResult = javaParser.parse(file);

        if (!parseResult.isSuccessful()) {
            throw new Exception("Failed to parse file");
        }

        CompilationUnit cu = parseResult.getResult().orElseThrow(() ->
            new RuntimeException("Parse result is empty"));
        JsonArray methods = new JsonArray();

        cu.findAll(MethodDeclaration.class).forEach(method -> {
            if (method.isPublic()) {
                methods.add(extractMethodInfo(method));
            }
        });

        return gson.toJson(methods);
    }

    /**
     * 命令行接口
     */
    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("Usage: CodeAnalyzer <command> <file_path>");
            System.err.println("Commands: analyze, publicMethods");
            System.exit(1);
        }

        String command = args[0];
        String filePath = args[1];

        CodeAnalyzer analyzer = new CodeAnalyzer();

        try {
            String result;
            switch (command) {
                case "analyze":
                    result = analyzer.analyzeFile(filePath);
                    break;
                case "publicMethods":
                    result = analyzer.getPublicMethods(filePath);
                    break;
                default:
                    System.err.println("Unknown command: " + command);
                    System.exit(1);
                    return;
            }
            System.out.println(result);
        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }
}
