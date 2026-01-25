package com.comet.analyzer;

import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NullLiteralExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.DoStmt;
import com.github.javaparser.ast.stmt.ForEachStmt;
import com.github.javaparser.ast.stmt.ForStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.SwitchEntry;
import com.github.javaparser.ast.stmt.TryStmt;
import com.github.javaparser.ast.stmt.WhileStmt;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

/**
 * Deep code analyzer for RAG knowledge extraction.
 * Analyzes code patterns, control flow, and dependencies.
 */
public class DeepAnalyzer {

    private final JavaParser javaParser;
    private final Gson gson;

    public DeepAnalyzer() {
        this.javaParser = new JavaParser();
        this.gson = new GsonBuilder().setPrettyPrinting().create();
    }

    /**
     * Perform deep analysis on a Java source file.
     *
     * @param filePath Path to the Java source file
     * @return JSON string with analysis results
     */
    public String analyzeDeep(String filePath) throws Exception {
        File file = new File(filePath);
        ParseResult<CompilationUnit> parseResult = javaParser.parse(file);

        if (!parseResult.isSuccessful()) {
            throw new Exception("Failed to parse file: " + filePath);
        }

        CompilationUnit cu = parseResult.getResult()
                .orElseThrow(() -> new RuntimeException("Parse result is empty"));

        JsonObject result = new JsonObject();

        // Package name
        cu.getPackageDeclaration().ifPresent(pd -> result.addProperty("package", pd.getNameAsString()));

        // Analyze each class
        JsonArray classes = new JsonArray();
        cu.findAll(ClassOrInterfaceDeclaration.class).forEach(cls -> {
            if (!cls.isInterface()) {
                JsonObject classAnalysis = analyzeClass(cls);
                classes.add(classAnalysis);
            }
        });
        result.add("classes", classes);

        return gson.toJson(result);
    }

    /**
     * Analyze a single class.
     */
    private JsonObject analyzeClass(ClassOrInterfaceDeclaration cls) {
        JsonObject classInfo = new JsonObject();
        classInfo.addProperty("name", cls.getNameAsString());
        classInfo.addProperty("isPublic", cls.isPublic());
        classInfo.addProperty("isAbstract", cls.isAbstract());

        // Fields
        JsonArray fields = new JsonArray();
        cls.getFields().forEach(field -> {
            field.getVariables().forEach(var -> {
                JsonObject fieldInfo = new JsonObject();
                fieldInfo.addProperty("name", var.getNameAsString());
                fieldInfo.addProperty("type", var.getTypeAsString());
                fieldInfo.addProperty("isStatic", field.isStatic());
                fieldInfo.addProperty("isFinal", field.isFinal());
                fields.add(fieldInfo);
            });
        });
        classInfo.add("fields", fields);

        // Methods with deep analysis
        JsonArray methods = new JsonArray();
        cls.getMethods().forEach(method -> {
            JsonObject methodAnalysis = analyzeMethod(method, cls);
            methods.add(methodAnalysis);
        });
        classInfo.add("methods", methods);

        return classInfo;
    }

    /**
     * Deep analysis of a method.
     */
    private JsonObject analyzeMethod(MethodDeclaration method, ClassOrInterfaceDeclaration cls) {
        JsonObject methodInfo = new JsonObject();

        // Basic info
        methodInfo.addProperty("name", method.getNameAsString());
        methodInfo.addProperty("signature", method.getDeclarationAsString(false, false, false));
        methodInfo.addProperty("returnType", method.getTypeAsString());
        methodInfo.addProperty("isPublic", method.isPublic());
        methodInfo.addProperty("isStatic", method.isStatic());

        // Parameters
        JsonArray params = new JsonArray();
        method.getParameters().forEach(param -> {
            JsonObject paramInfo = new JsonObject();
            paramInfo.addProperty("name", param.getNameAsString());
            paramInfo.addProperty("type", param.getTypeAsString());
            params.add(paramInfo);
        });
        methodInfo.add("parameters", params);

        // Line range
        method.getRange().ifPresent(range -> {
            JsonObject rangeInfo = new JsonObject();
            rangeInfo.addProperty("begin", range.begin.line);
            rangeInfo.addProperty("end", range.end.line);
            methodInfo.add("range", rangeInfo);
        });

        // Javadoc
        method.getJavadocComment().ifPresent(javadoc -> methodInfo.addProperty("javadoc", javadoc.getContent()));

        // Deep analysis
        methodInfo.add("nullChecks", analyzeNullChecks(method));
        methodInfo.add("boundaryChecks", analyzeBoundaryChecks(method));
        methodInfo.add("exceptionHandling", analyzeExceptionHandling(method));
        methodInfo.add("methodCalls", analyzeMethodCalls(method));
        methodInfo.add("fieldAccess", analyzeFieldAccess(method, cls));
        methodInfo.addProperty("cyclomaticComplexity", calculateCyclomaticComplexity(method));

        return methodInfo;
    }

    /**
     * Analyze null check patterns in the method.
     */
    private JsonArray analyzeNullChecks(MethodDeclaration method) {
        JsonArray nullChecks = new JsonArray();

        method.findAll(IfStmt.class).forEach(ifStmt -> {
            Expression condition = ifStmt.getCondition();
            List<String> nullCheckedVars = findNullCheckedVariables(condition);

            if (!nullCheckedVars.isEmpty()) {
                JsonObject check = new JsonObject();
                check.addProperty("type", "if_null_check");
                check.addProperty("condition", condition.toString());
                JsonArray vars = new JsonArray();
                nullCheckedVars.forEach(vars::add);
                check.add("variables", vars);

                ifStmt.getRange().ifPresent(range -> check.addProperty("line", range.begin.line));

                nullChecks.add(check);
            }
        });

        return nullChecks;
    }

    /**
     * Find variables being checked for null.
     */
    private List<String> findNullCheckedVariables(Expression expr) {
        List<String> result = new ArrayList<>();

        expr.findAll(BinaryExpr.class).forEach(binary -> {
            if (binary.getOperator() == BinaryExpr.Operator.EQUALS ||
                    binary.getOperator() == BinaryExpr.Operator.NOT_EQUALS) {

                Expression left = binary.getLeft();
                Expression right = binary.getRight();

                // Check if one side is null
                if (left instanceof NullLiteralExpr) {
                    result.add(extractVariableName(right));
                } else if (right instanceof NullLiteralExpr) {
                    result.add(extractVariableName(left));
                }
            }
        });

        return result;
    }

    /**
     * Extract variable name from expression.
     */
    private String extractVariableName(Expression expr) {
        if (expr instanceof NameExpr) {
            return ((NameExpr) expr).getNameAsString();
        } else if (expr instanceof FieldAccessExpr) {
            return ((FieldAccessExpr) expr).getNameAsString();
        }
        return expr.toString();
    }

    /**
     * Analyze boundary check patterns.
     */
    private JsonArray analyzeBoundaryChecks(MethodDeclaration method) {
        JsonArray boundaryChecks = new JsonArray();

        method.findAll(IfStmt.class).forEach(ifStmt -> {
            Expression condition = ifStmt.getCondition();
            List<JsonObject> checks = findBoundaryChecks(condition);

            checks.forEach(check -> {
                ifStmt.getRange().ifPresent(range -> check.addProperty("line", range.begin.line));
                boundaryChecks.add(check);
            });
        });

        return boundaryChecks;
    }

    /**
     * Find boundary check expressions.
     */
    private List<JsonObject> findBoundaryChecks(Expression expr) {
        List<JsonObject> result = new ArrayList<>();

        expr.findAll(BinaryExpr.class).forEach(binary -> {
            BinaryExpr.Operator op = binary.getOperator();

            // Check for comparison operators
            if (op == BinaryExpr.Operator.LESS ||
                    op == BinaryExpr.Operator.LESS_EQUALS ||
                    op == BinaryExpr.Operator.GREATER ||
                    op == BinaryExpr.Operator.GREATER_EQUALS) {

                JsonObject check = new JsonObject();
                check.addProperty("type", "comparison");
                check.addProperty("operator", op.asString());
                check.addProperty("left", binary.getLeft().toString());
                check.addProperty("right", binary.getRight().toString());

                // Check for common patterns
                String leftStr = binary.getLeft().toString();
                String rightStr = binary.getRight().toString();

                if (leftStr.contains(".length") || rightStr.contains(".length") ||
                        leftStr.contains(".size()") || rightStr.contains(".size()")) {
                    check.addProperty("pattern", "array_or_collection_bounds");
                } else if (rightStr.equals("0") || leftStr.equals("0")) {
                    check.addProperty("pattern", "zero_check");
                }

                result.add(check);
            }
        });

        return result;
    }

    /**
     * Analyze exception handling.
     */
    private JsonObject analyzeExceptionHandling(MethodDeclaration method) {
        JsonObject exceptionInfo = new JsonObject();

        // Try-catch blocks
        JsonArray tryCatches = new JsonArray();
        method.findAll(TryStmt.class).forEach(tryStmt -> {
            JsonObject tryInfo = new JsonObject();

            // Resources (try-with-resources)
            if (!tryStmt.getResources().isEmpty()) {
                tryInfo.addProperty("hasResources", true);
                JsonArray resources = new JsonArray();
                tryStmt.getResources().forEach(r -> resources.add(r.toString()));
                tryInfo.add("resources", resources);
            } else {
                tryInfo.addProperty("hasResources", false);
            }

            // Catch clauses
            JsonArray catches = new JsonArray();
            tryStmt.getCatchClauses().forEach(catchClause -> {
                JsonObject catchInfo = new JsonObject();
                catchInfo.addProperty("exceptionType", catchClause.getParameter().getTypeAsString());
                catchInfo.addProperty("paramName", catchClause.getParameter().getNameAsString());

                // Check if exception is just swallowed
                boolean isSwallowed = catchClause.getBody().getStatements().isEmpty();
                catchInfo.addProperty("isSwallowed", isSwallowed);

                catches.add(catchInfo);
            });
            tryInfo.add("catches", catches);

            // Finally block
            tryInfo.addProperty("hasFinally", tryStmt.getFinallyBlock().isPresent());

            tryStmt.getRange().ifPresent(range -> tryInfo.addProperty("line", range.begin.line));

            tryCatches.add(tryInfo);
        });
        exceptionInfo.add("tryCatchBlocks", tryCatches);

        // Thrown exceptions (declared)
        JsonArray thrownExceptions = new JsonArray();
        method.getThrownExceptions().forEach(ex -> thrownExceptions.add(ex.asString()));
        exceptionInfo.add("thrownExceptions", thrownExceptions);

        return exceptionInfo;
    }

    /**
     * Analyze method calls within the method.
     */
    private JsonArray analyzeMethodCalls(MethodDeclaration method) {
        JsonArray methodCalls = new JsonArray();

        method.findAll(MethodCallExpr.class).forEach(call -> {
            JsonObject callInfo = new JsonObject();
            callInfo.addProperty("methodName", call.getNameAsString());

            // Scope (object or class the method is called on)
            call.getScope().ifPresent(scope -> callInfo.addProperty("scope", scope.toString()));

            // Arguments
            JsonArray args = new JsonArray();
            call.getArguments().forEach(arg -> args.add(arg.toString()));
            callInfo.add("arguments", args);

            call.getRange().ifPresent(range -> callInfo.addProperty("line", range.begin.line));

            methodCalls.add(callInfo);
        });

        return methodCalls;
    }

    /**
     * Analyze field access within the method.
     */
    private JsonArray analyzeFieldAccess(MethodDeclaration method, ClassOrInterfaceDeclaration cls) {
        JsonArray fieldAccess = new JsonArray();

        // Get class field names
        Set<String> classFields = new HashSet<>();
        cls.getFields().forEach(field -> field.getVariables().forEach(var -> classFields.add(var.getNameAsString())));

        // Find field access expressions
        method.findAll(NameExpr.class).forEach(nameExpr -> {
            String name = nameExpr.getNameAsString();
            if (classFields.contains(name)) {
                JsonObject access = new JsonObject();
                access.addProperty("fieldName", name);
                access.addProperty("isRead", isReadAccess(nameExpr));
                access.addProperty("isWrite", isWriteAccess(nameExpr));

                nameExpr.getRange().ifPresent(range -> access.addProperty("line", range.begin.line));

                fieldAccess.add(access);
            }
        });

        // Also check for this.field access
        method.findAll(FieldAccessExpr.class).forEach(fieldExpr -> {
            if (fieldExpr.getScope().toString().equals("this")) {
                String fieldName = fieldExpr.getNameAsString();
                JsonObject access = new JsonObject();
                access.addProperty("fieldName", fieldName);
                access.addProperty("isRead", isReadAccess(fieldExpr));
                access.addProperty("isWrite", isWriteAccess(fieldExpr));

                fieldExpr.getRange().ifPresent(range -> access.addProperty("line", range.begin.line));

                fieldAccess.add(access);
            }
        });

        return fieldAccess;
    }

    /**
     * Check if expression is a read access.
     */
    private boolean isReadAccess(Expression expr) {
        // Check if parent is an assignment target
        Optional<Node> parent = expr.getParentNode();
        if (parent.isPresent()) {
            Node p = parent.get();
            if (p instanceof com.github.javaparser.ast.expr.AssignExpr) {
                com.github.javaparser.ast.expr.AssignExpr assign = (com.github.javaparser.ast.expr.AssignExpr) p;
                // If this is the target, it's a write (not just read)
                if (assign.getTarget() == expr) {
                    return false;
                }
            }
        }
        return true;
    }

    /**
     * Check if expression is a write access.
     */
    private boolean isWriteAccess(Expression expr) {
        Optional<Node> parent = expr.getParentNode();
        if (parent.isPresent()) {
            Node p = parent.get();
            if (p instanceof com.github.javaparser.ast.expr.AssignExpr) {
                com.github.javaparser.ast.expr.AssignExpr assign = (com.github.javaparser.ast.expr.AssignExpr) p;
                return assign.getTarget() == expr;
            } else if (p instanceof com.github.javaparser.ast.expr.UnaryExpr) {
                com.github.javaparser.ast.expr.UnaryExpr unary = (com.github.javaparser.ast.expr.UnaryExpr) p;
                // ++, --, etc.
                return unary.getOperator().name().startsWith("PREFIX_") ||
                        unary.getOperator().name().startsWith("POSTFIX_");
            }
        }
        return false;
    }

    /**
     * Calculate cyclomatic complexity of a method.
     * CC = E - N + 2P, simplified as: 1 + number of decision points
     */
    private int calculateCyclomaticComplexity(MethodDeclaration method) {
        int complexity = 1; // Base complexity

        // Count decision points
        complexity += method.findAll(IfStmt.class).size();
        complexity += method.findAll(ForStmt.class).size();
        complexity += method.findAll(ForEachStmt.class).size();
        complexity += method.findAll(WhileStmt.class).size();
        complexity += method.findAll(DoStmt.class).size();
        complexity += method.findAll(CatchClause.class).size();

        // Switch cases (each case except default adds complexity)
        for (SwitchEntry entry : method.findAll(SwitchEntry.class)) {
            if (!entry.getLabels().isEmpty()) {
                complexity++;
            }
        }

        // Logical operators (&&, ||) add complexity
        for (BinaryExpr binary : method.findAll(BinaryExpr.class)) {
            if (binary.getOperator() == BinaryExpr.Operator.AND ||
                    binary.getOperator() == BinaryExpr.Operator.OR) {
                complexity++;
            }
        }

        // Ternary operators
        complexity += method.findAll(com.github.javaparser.ast.expr.ConditionalExpr.class).size();

        return complexity;
    }

    /**
     * Analyze a specific method by name.
     */
    public String analyzeMethod(String filePath, String className, String methodName) throws Exception {
        File file = new File(filePath);
        ParseResult<CompilationUnit> parseResult = javaParser.parse(file);

        if (!parseResult.isSuccessful()) {
            throw new Exception("Failed to parse file: " + filePath);
        }

        CompilationUnit cu = parseResult.getResult()
                .orElseThrow(() -> new RuntimeException("Parse result is empty"));

        for (ClassOrInterfaceDeclaration cls : cu.findAll(ClassOrInterfaceDeclaration.class)) {
            if (cls.getNameAsString().equals(className)) {
                for (MethodDeclaration method : cls.getMethods()) {
                    if (method.getNameAsString().equals(methodName)) {
                        JsonObject result = analyzeMethod(method, cls);
                        result.addProperty("className", className);
                        return gson.toJson(result);
                    }
                }
            }
        }

        throw new Exception("Method not found: " + className + "." + methodName);
    }

    /**
     * Command line interface
     */
    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("Usage: DeepAnalyzer <command> <file_path> [class_name] [method_name]");
            System.err.println("Commands:");
            System.err.println("  analyzeDeep <file_path>           - Deep analysis of entire file");
            System.err.println("  analyzeMethod <file_path> <class> <method> - Analyze specific method");
            System.exit(1);
        }

        String command = args[0];
        String filePath = args[1];

        DeepAnalyzer analyzer = new DeepAnalyzer();

        try {
            String result;
            switch (command) {
                case "analyzeDeep":
                    result = analyzer.analyzeDeep(filePath);
                    break;
                case "analyzeMethod":
                    if (args.length < 4) {
                        System.err.println("analyzeMethod requires: file_path class_name method_name");
                        System.exit(1);
                        return;
                    }
                    result = analyzer.analyzeMethod(filePath, args[2], args[3]);
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
