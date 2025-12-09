package com.comet.models;

import com.google.gson.annotations.SerializedName;

public class MutationPatch {
    @SerializedName("file_path")
    private String filePath;

    @SerializedName("line_start")
    private int lineStart;

    @SerializedName("line_end")
    private int lineEnd;

    @SerializedName("original")
    private String originalCode;

    @SerializedName("mutated")
    private String mutatedCode;

    public MutationPatch() {
    }

    public MutationPatch(String filePath, int lineStart, int lineEnd, String originalCode, String mutatedCode) {
        this.filePath = filePath;
        this.lineStart = lineStart;
        this.lineEnd = lineEnd;
        this.originalCode = originalCode;
        this.mutatedCode = mutatedCode;
    }

    public String getFilePath() {
        return filePath;
    }

    public void setFilePath(String filePath) {
        this.filePath = filePath;
    }

    public int getLineStart() {
        return lineStart;
    }

    public void setLineStart(int lineStart) {
        this.lineStart = lineStart;
    }

    public int getLineEnd() {
        return lineEnd;
    }

    public void setLineEnd(int lineEnd) {
        this.lineEnd = lineEnd;
    }

    public String getOriginalCode() {
        return originalCode;
    }

    public void setOriginalCode(String originalCode) {
        this.originalCode = originalCode;
    }

    public String getMutatedCode() {
        return mutatedCode;
    }

    public void setMutatedCode(String mutatedCode) {
        this.mutatedCode = mutatedCode;
    }
}
