package com.comet.formatter;

import com.google.googlejavaformat.java.Formatter;
import com.google.googlejavaformat.java.FormatterException;
import com.google.googlejavaformat.java.JavaFormatterOptions;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

public class JavaFormatter {

    private final Formatter formatter;

    public JavaFormatter() {
        this.formatter = new Formatter(
            JavaFormatterOptions.builder()
                .style(JavaFormatterOptions.Style.GOOGLE)
                .build()
        );
    }

    public JavaFormatter(String style) {
        JavaFormatterOptions.Style formatStyle = "AOSP".equalsIgnoreCase(style)
            ? JavaFormatterOptions.Style.AOSP
            : JavaFormatterOptions.Style.GOOGLE;

        this.formatter = new Formatter(
            JavaFormatterOptions.builder()
                .style(formatStyle)
                .build()
        );
    }

    public String formatSource(String source) throws FormatterException {
        return formatter.formatSource(source);
    }

    public void formatFile(String filePath) throws IOException, FormatterException {
        Path path = Paths.get(filePath);
        byte[] bytes = Files.readAllBytes(path);
        String source = new String(bytes, StandardCharsets.UTF_8);
        String formatted = formatSource(source);
        Files.write(path, formatted.getBytes(StandardCharsets.UTF_8));
    }

    public String formatFileAndReturn(String filePath) throws IOException, FormatterException {
        Path path = Paths.get(filePath);
        byte[] bytes = Files.readAllBytes(path);
        String source = new String(bytes, StandardCharsets.UTF_8);
        return formatSource(source);
    }

    public static void main(String[] args) {
        if (args.length < 1) {
            System.err.println("Usage: java com.comet.formatter.JavaFormatter <file> [--style GOOGLE|AOSP] [--replace]");
            System.exit(1);
        }

        String filePath = args[0];
        String style = "GOOGLE";
        boolean replace = false;

        for (int i = 1; i < args.length; i++) {
            if ("--style".equals(args[i]) && i + 1 < args.length) {
                style = args[++i];
            } else if ("--replace".equals(args[i])) {
                replace = true;
            }
        }

        try {
            JavaFormatter formatter = new JavaFormatter(style);

            if (replace) {
                formatter.formatFile(filePath);
                System.out.println("Formatted: " + filePath);
            } else {
                String formatted = formatter.formatFileAndReturn(filePath);
                System.out.println(formatted);
            }
        } catch (IOException e) {
            System.err.println("IO Error: " + e.getMessage());
            System.exit(1);
        } catch (FormatterException e) {
            System.err.println("Format Error: " + e.getMessage());
            System.exit(1);
        }
    }
}
