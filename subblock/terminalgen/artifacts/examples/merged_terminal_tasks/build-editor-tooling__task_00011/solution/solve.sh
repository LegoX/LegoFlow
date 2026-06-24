#!/bin/bash
# Solution: Fix OpenMP Linker Errors in Makefile
# This script identifies and fixes the missing OpenMP linker configuration

set -e  # Exit on any error

WORKDIR="/app/task_file"
cd "$WORKDIR" || exit 1

echo "=========================================="
echo "Terminal Bench: Fixing OpenMP Linker Errors"
echo "=========================================="
echo ""

# Step 1: Examine the broken Makefile
echo "[STEP 1] Examining the broken Makefile"
echo "Current LDFLAGS and LDLIBS configuration:"
grep -E "^LDFLAGS|^LDLIBS" Makefile
echo ""

# Step 2: Identify the issues
echo "[STEP 2] Identifying linker configuration issues"
echo "Issue found:"
echo "  - LDFLAGS is empty (missing -fopenmp for linking)"
echo "  - LDLIBS is empty (OpenMP runtime library needed)"
echo "  - Missing .PHONY declarations"
echo ""

# Step 3: Create backup and fix the Makefile
echo "[STEP 3] Fixing the Makefile"
cp Makefile Makefile.bak
echo "Backup saved: Makefile.bak"

# Fix LDFLAGS - add -fopenmp for OpenMP linking
sed -i 's/^LDFLAGS = $/LDFLAGS = -fopenmp/' Makefile

# Add .PHONY declarations if not present
if ! grep -q "^\.PHONY" Makefile; then
    # Determine which targets exist in Makefile
    {
        echo ""
        echo ".PHONY: all clean"
    } >> Makefile
fi

echo "Makefile fixed with:"
echo "  ✓ LDFLAGS = -fopenmp (added OpenMP linker flag)"
echo "  ✓ .PHONY declarations added"
echo ""

# Step 4: Verify the fix
echo "[STEP 4] Verifying Makefile changes"
echo "Updated linker configuration:"
grep -E "^LDFLAGS|^LDLIBS" Makefile
echo ""

# Step 5: Clean build
echo "[STEP 5] Building the project"
echo "Running: make clean && make"
echo "---"
make clean && make
BUILD_EXIT=$?
echo "---"

if [ $BUILD_EXIT -ne 0 ]; then
    echo "✗ Build failed with exit code $BUILD_EXIT"
    exit 1
fi
echo "✓ Build completed successfully"
echo ""

# Step 6: Verify binary exists
echo "[STEP 6] Verifying output binary"
if [ ! -f "output/worker_app" ]; then
    echo "✗ Error: Binary not found at output/worker_app"
    exit 1
fi
echo "✓ Binary created: output/worker_app"
ls -lh output/worker_app
echo ""

# Step 7: Test the compiled binary
echo "[STEP 7] Testing the compiled binary"
echo "Running: ./output/worker_app"
echo "---"
if ./output/worker_app; then
    TEST_EXIT=0
    echo "---"
    echo "✓ Binary executed successfully"
else
    TEST_EXIT=$?
    echo "---"
    echo "✗ Binary execution failed with exit code $TEST_EXIT"
    exit 1
fi
echo ""

# Step 8: Summary
echo "=========================================="
echo "✓ TASK COMPLETE - OpenMP Linking Fixed!"
echo "=========================================="
echo "Summary:"
echo "  ✓ Makefile corrected"
echo "  ✓ OpenMP linker flag (-fopenmp) added to LDFLAGS"
echo "  ✓ Project built successfully"
echo "  ✓ Binary created at output/worker_app"
echo "  ✓ Binary tested and runs without errors"
echo "=========================================="

exit 0