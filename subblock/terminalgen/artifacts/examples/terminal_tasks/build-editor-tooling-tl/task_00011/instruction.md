# Terminal Bench Task: Fixing OpenMP Linker Errors in Makefile

## Task Description

You have a C++ project with OpenMP support that compiles successfully using a direct `g++` command, but fails when built using a Makefile with the linker error:

```
undefined reference to symbol 'GOMP_parallel@@GOMP_4.0'
DSO missing from command line
```

This is a common issue where the Makefile doesn't properly link the OpenMP runtime library. Your task is to identify and fix the Makefile configuration so that the project builds successfully.

## Project Structure

The project is located at `/app/task_file/` with the following structure:

```
/app/task_file/
├── src/
│   ├── main.cpp
│   ├── worker.cpp
│   └── worker.h
├── Makefile (broken - needs fixing)
└── output/
    └── (binary output will be placed here)
```

## Source Files

### `/app/task_file/src/main.cpp`
A C++11 program with OpenMP parallel sections that performs simple computations.

### `/app/task_file/src/worker.cpp` and `/app/task_file/src/worker.h`
Support files containing functions that use OpenMP pragmas (`#pragma omp parallel`).

## The Broken Makefile

The current `/app/task_file/Makefile` contains the following issues:

1. **Missing OpenMP linker flag**: The `-fopenmp` flag is used during compilation but the corresponding `-lgomp` library is not linked
2. **Incorrect compiler flag placement**: Some linker-specific flags are incorrectly placed in compilation-only variables
3. **Library ordering**: The standard library linking flag is in the wrong variable

## Your Task

1. **Examine** the current Makefile and identify all linker configuration issues
2. **Fix** the Makefile by:
   - Adding the OpenMP runtime library (`-lgomp`) to the appropriate linker flags
   - Ensuring all compilation flags are in the compilation step
   - Ensuring all linker flags/libraries are in the linking step
   - Maintaining proper flag ordering for successful linking
3. **Verify** the fix by building the project with `make clean && make`
4. **Test** that the compiled binary runs successfully and produces output

## Working Directory

```bash
cd /app/task_file/
```

## Success Criteria

✅ The Makefile is corrected to properly configure OpenMP linking  
✅ Running `make clean && make` produces no linker errors  
✅ The output binary is created at `/app/task_file/output/worker_app`  
✅ Running `./output/worker_app` executes successfully without runtime errors  
✅ The program output demonstrates parallel processing (check for OpenMP thread messages)

## Hints

- The error message mentions `GOMP_parallel@@GOMP_4.0`, which is the GNU OpenMP runtime symbol
- The working command-line example includes `-lgomp` at the linking stage, but the Makefile doesn't
- In Makefiles, CPPFLAGS and CXXFLAGS are for compilation, while LDFLAGS and LDLIBS control linking
- The order of libraries matters: OpenMP library should be linked alongside other system libraries

## Expected Outcome

After fixing the Makefile, you should be able to:
1. Successfully compile all source files to object files
2. Successfully link the object files into the final binary
3. Run the binary and see output confirming parallel execution

The fixed Makefile should be a minimal, correct version that demonstrates proper handling of OpenMP compilation and linking flags.