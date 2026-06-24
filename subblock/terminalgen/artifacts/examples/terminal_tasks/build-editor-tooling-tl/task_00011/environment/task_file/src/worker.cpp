#include "worker.h"
#include <iostream>
#include <omp.h>

void parallel_work(int n) {
    #pragma omp parallel for
    for (int i = 0; i < n; i++) {
        #pragma omp critical
        {
            std::cout << "Thread " << omp_get_thread_num() 
                      << " processing iteration " << i << std::endl;
        }
    }
}
